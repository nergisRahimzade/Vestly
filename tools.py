import hashlib
import json
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from calculator import (
    calculate_us_payroll, calculate_uk_payroll, calculate_germany_payroll,
    proration_fraction, shares_vested, months_between, equity_expense_for_period,
)
from journal import money, payroll_entry, equity_expense_entry, forfeiture_reversal_entry
DB = "company.db"

# ---------- 1. One standard shape for every answer ----------
def reply(status, data=None, warnings=None, error=None):
  return {"status": status, "data": data, "warnings": warnings or [], "error": error }

# ---------- 2. Database helpers ----------
def _query(sql, params=()):
  """Read-only connection: changing data trhough this is impossible."""
  con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
  con.row_factory = sqlite3.Row

  try:
    return [dict(r) for r in con.execute(sql, params).fetchall()]
  finally:
    con.close()

def init_tools_db():
  con = sqlite3.connect(DB)
  con.executescript("""
  CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT, actor TEXT, tool TEXT,
    inputs TEXT, status TEXT, output TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
  """)

  try:
    con.execute("ALTER TABLE audit_log ADD COLUMN reason TEXT")
  except sqlite3.OperationalError:
    pass   # column already exists

  con.commit()
  con.close()

def _log(actor, tool_name, args, result, reason=None):
  con = sqlite3.connect(DB)
  con.execute(
    "INSERT INTO audit_log (at, actor, tool, inputs, status, output, reason) VALUES (?,?,?,?,?,?,?)",
    (datetime.now(timezone.utc).isoformat(), actor, tool_name,
      json.dumps(args, default=str), result["status"],
      json.dumps(result, default=str)[:2000], reason),
  )
  con.commit()
  con.close()
  return result

# ---------- 3. The menu of tools and the gatekeeper ----------
TOOLS = {}

class ToolInput(BaseModel):
  model_config = ConfigDict(extra="forbid")  # reject inputs nobody declared
  reason: str | None = Field(
    default=None,
    description="One or two sentences: why you are making this call and what you will do with the result.")

def tool(name, input_model, kind):
  """Decorator: adds a function to the menu. kind = 'read', 'compute' or 'act'."""
  def register(fn):
    TOOLS[name] = {"fn": fn, "input": input_model, "kind": kind}
    return fn
  return register

def call_tool(name, args, actor="agent"):
  """The ONLY way the agent runs anything. Validates, runs, catches errors, logs."""
  spec = TOOLS.get(name)
  if spec is None:
    return _log(actor, name, args, reply("error", error=f"Unknown tool: {name}"))
  try:
    parsed = spec["input"](**args)
  except ValidationError as e:
    msg = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
    return _log(actor, name, args, reply("error", error=f"Bad input: {msg}"), args.get("reason"))

  reason = parsed.reason
  if spec["kind"] == "act" and not reason:
    return _log(actor, name, args, reply("error", error="Act tools require a reason"), None)

  try:
    result = spec["fn"](parsed)
  except Exception as e:
    result = reply("error", error=f"{type(e).__name__}: {e}")
  return _log(actor, name, args, result, reason)

def tool_specs():
  """The menu in the format an AI model expects (we'll use this in Step 5)."""
  return [{"name": n, "description": s["fn"].__doc__, "input_schema": s["input"].model_json_schema()}
    for n, s in TOOLS.items()]


init_tools_db()

# ================= READ TOOLS =================

class GetEmployeeIn(ToolInput):
    employee_id: int

@tool("get_employee", GetEmployeeIn, kind="read")
def get_employee(a):
  """Look up one employee by id: entity, department, title, dates and current salary."""
  rows = _query("""
      SELECT e.employee_id, e.entity_id, e.full_name, e.department, e.title,
              e.hire_date, e.termination_date, c.annual_salary, c.currency
      FROM employees e JOIN compensation c USING (employee_id)
      WHERE e.employee_id = ?""", (a.employee_id,))
  if not rows:
      return reply("error", error=f"No employee with id {a.employee_id}")
  warnings = []
  if rows[0]["termination_date"]:
      warnings.append(f"Employee was terminated on {rows[0]['termination_date']}")
  return reply("ok", data=rows[0], warnings=warnings)


class ListEmployeesIn(ToolInput):
  entity_id: Literal["US", "UK", "DE"]
  active_only: bool = True
  limit: int = Field(default=25, ge=1, le=100)

@tool("list_employees_by_entity", ListEmployeesIn, kind="read")
def list_employees_by_entity(a):
  """List employees of one entity (US, UK or DE). Returns at most `limit` rows plus the total count."""
  where = "e.entity_id = ?" + (" AND e.termination_date IS NULL" if a.active_only else "")
  total = _query(f"SELECT COUNT(*) AS n FROM employees e WHERE {where}", (a.entity_id,))[0]["n"]
  rows = _query(f"""
      SELECT e.employee_id, e.full_name, e.department, e.title, e.hire_date, e.termination_date
      FROM employees e WHERE {where} ORDER BY e.employee_id LIMIT ?""", (a.entity_id, a.limit))
  warnings = []
  if total > len(rows):
      warnings.append(f"Showing {len(rows)} of {total}. Narrow the search or raise the limit.")
  return reply("ok", data={"total": total, "employees": rows}, warnings=warnings)


class GetGrantIn(ToolInput):
  employee_id: int

@tool("get_grant", GetGrantIn, kind="read")
def get_grant(a):
  """Get all stock-option grants for one employee."""
  rows = _query("SELECT * FROM grants WHERE employee_id = ? ORDER BY grant_date", (a.employee_id,))
  warnings = [] if rows else ["This employee has no grants"]
  return reply("ok", data=rows, warnings=warnings)


class GetFxRateIn(ToolInput):
  currency: Literal["USD", "GBP", "EUR"]

@tool("get_fx_rate", GetFxRateIn, kind="read")
def get_fx_rate(a):
  """Latest saved exchange rate: how many units of the currency equal 1 USD, with its timestamp."""
  import fx    # imported here so the other tools still work if .env is missing
  rate, fetched_at = fx.get_rate(a.currency)
  warnings = []
  if fetched_at:
      age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds() / 3600
      if age_h > 3:
          warnings.append(f"Rate is {age_h:.1f} hours old")
  return reply("ok", data={"currency": a.currency, "per_usd": rate, "fetched_at": fetched_at},
                warnings=warnings)

# ================= COMPUTE TOOLS =================

CALC = {"US": calculate_us_payroll, "UK": calculate_uk_payroll, "DE": calculate_germany_payroll}
PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
MONEY_KEYS = ("gross", "withholding", "net", "employer_taxes")

def _d(s):
  return date.fromisoformat(s) if s else None

def _strs(d):
  """Make a dict JSON-friendly: money (Decimal) becomes text, counts stay numbers."""
  return {k: (v if isinstance(v, int) else str(v)) for k, v in d.items()}

def _payroll_by_department(entity_id, period):
  """Shared helper: walk through every employee of an entity and add up one month of payroll."""
  year, month = int(period[:4]), int(period[5:7])
  currency = _query("SELECT currency FROM entities WHERE entity_id = ?", (entity_id,))[0]["currency"]
  rows = _query("""
      SELECT e.employee_id, e.department, e.hire_date, e.termination_date, c.annual_salary
      FROM employees e JOIN compensation c USING (employee_id)
      WHERE e.entity_id = ? ORDER BY e.employee_id""", (entity_id,))
  depts, partial = {}, []
  for r in rows:
      frac = proration_fraction(_d(r["hire_date"]), _d(r["termination_date"]), year, month)
      if frac == 0:
          continue                                   # not employed that month
      p = CALC[entity_id](r["annual_salary"], frac)
      d = depts.setdefault(r["department"],
                            {"headcount": 0, **{k: Decimal("0.00") for k in MONEY_KEYS}})
      d["headcount"] += 1
      for k in MONEY_KEYS:
          d[k] += p[k]
      if frac < 1:
          partial.append({"employee_id": r["employee_id"], "worked": f"{frac:.4f}"})
  return currency, depts, partial


class RunPayrollIn(ToolInput):
  entity_id: Literal["US", "UK", "DE"]
  period: str = Field(pattern=PERIOD_PATTERN, description="Month as YYYY-MM, e.g. 2026-09")

@tool("run_payroll", RunPayrollIn, kind="compute")
def run_payroll(a):
  """Calculate one month of payroll for every employee of one entity. Returns totals overall and by department. Saves and posts nothing."""
  currency, depts, partial = _payroll_by_department(a.entity_id, a.period)
  if not depts:
      return reply("error", error=f"No one was employed in {a.entity_id} in {a.period}")
  totals = {k: sum((d[k] for d in depts.values()), Decimal("0.00")) for k in MONEY_KEYS}
  data = {
      "entity_id": a.entity_id, "period": a.period, "currency": currency,
      "headcount": sum(d["headcount"] for d in depts.values()),
      "totals": _strs(totals),
      "by_department": {n: _strs(d) for n, d in sorted(depts.items())},
      "partial_month_sample": partial[:10],
  }
  warnings = [f"{len(partial)} employee(s) worked only part of the month (prorated)"] if partial else []
  return reply("ok", data=data, warnings=warnings)


class CalculateVestingIn(ToolInput):
  employee_id: int
  as_of: date

@tool("calculate_vesting", CalculateVestingIn, kind="compute")
def calculate_vesting(a):
  """For one employee, how many options have vested as of a date, per grant. Vesting stops when the employee leaves."""
  emp = _query("SELECT termination_date FROM employees WHERE employee_id = ?", (a.employee_id,))
  if not emp:
      return reply("error", error=f"No employee with id {a.employee_id}")
  warnings, as_of = [], a.as_of
  left = _d(emp[0]["termination_date"])
  if left and left < as_of:
      as_of = left
      warnings.append(f"Employee left on {left}; vesting stopped then and the rest is forfeited")
  grants = _query("SELECT * FROM grants WHERE employee_id = ? ORDER BY grant_id", (a.employee_id,))
  if not grants:
      warnings.append("This employee has no grants")
  out = []
  for g in grants:
      start = _d(g["grant_date"])
      vested = shares_vested(g["options_granted"], start, as_of, g["vest_years"], g["cliff_months"])
      months = months_between(start, as_of)
      status = ("before cliff" if months < g["cliff_months"]
                else "fully vested" if vested == g["options_granted"] else "vesting")
      out.append({"grant_id": g["grant_id"], "options_granted": g["options_granted"],
                  "vested": vested, "unvested": g["options_granted"] - vested,
                  "months_since_grant": months, "status": status})
  return reply("ok", data={"employee_id": a.employee_id, "as_of": str(as_of), "grants": out},
                warnings=warnings)

def _usd_to_local_rate(currency, period, period_end):
  """The months fixed USD -> local rate. Returns (rate, info_for_audit_trail, warnings)."""
  if currency == "USD":
      return Decimal("1"), {"from": "USD", "to": "USD", "rate": "1", "source": "same currency"}, []
  import fx    # imported here so the other tools still work if .env is missing
  rate, source, warning = fx.get_period_rate(currency, period, period_end)
  info = {"from": "USD", "to": currency, "rate": str(rate), "source": source,
          "applies_to": "equity expense (grants are valued in USD)"}
  return Decimal(str(rate)), info, ([warning] if warning else [])

class GenerateEntriesIn(ToolInput):
  entity_id: Literal["US", "UK", "DE"]
  period: str = Field(pattern=PERIOD_PATTERN, description="Month as YYYY-MM, e.g. 2026-09")

@tool("generate_journal_entries", GenerateEntriesIn, kind="compute")
def generate_journal_entries(a):
  """Draft (do NOT post) the balanced journal entries for one entity and month: payroll and equity expense per department, plus forfeiture reversals. Equity values are converted from USD to the entity's currency. Returns a fingerprint of the draft."""
  year, month = int(a.period[:4]), int(a.period[5:7])
  period_end = date(year, month, monthrange(year, month)[1])
  currency, depts, _ = _payroll_by_department(a.entity_id, a.period)

  entries = []
  for dept, d in sorted(depts.items()):
      entries.append(payroll_entry(a.entity_id, currency, a.period, d["gross"],
                                    d["employer_taxes"], d["withholding"], d["net"],
                                    department=dept))

  # Equity expense is worked out in USD first, then translated
  equity_usd = {}
  grants = _query("""
      SELECT g.*, e.department, e.termination_date
      FROM grants g JOIN employees e USING (employee_id)
      WHERE e.entity_id = ? ORDER BY g.grant_id""", (a.entity_id,))
  for g in grants:
      r = equity_expense_for_period(g["options_granted"], g["fair_value_per_option"],
                                    _d(g["grant_date"]), period_end, g["vest_years"],
                                    g["cliff_months"], termination_date=_d(g["termination_date"]))
      d = equity_usd.setdefault(g["department"], {"expense": Decimal("0.00"), "reversal": Decimal("0.00")})
      d["expense"] += r["expense"]
      d["reversal"] += r["reversal"]

  warnings, fx_info = [], None
  if any(d["expense"] > 0 or d["reversal"] > 0 for d in equity_usd.values()):
      rate, fx_info, warnings = _usd_to_local_rate(currency, a.period, period_end)   # may raise: no rate = stop
      for dept, d in sorted(equity_usd.items()):
          if d["expense"] > 0:
              entries.append(equity_expense_entry(a.entity_id, currency, a.period,
                                                  money(d["expense"] * rate), department=dept))
          if d["reversal"] > 0:
              entries.append(forfeiture_reversal_entry(a.entity_id, currency, a.period,
                                                        money(d["reversal"] * rate), department=dept))

  if not entries:
      return reply("error", error=f"Nothing to draft for {a.entity_id} in {a.period}")

  dumped = [e.model_dump(mode="json") for e in entries]
  fingerprint = hashlib.sha256(json.dumps(dumped, sort_keys=True).encode()).hexdigest()[:16]
  total = sum((l.debit for e in entries for l in e.lines), Decimal("0.00"))
  data = {"fingerprint": fingerprint, "entity_id": a.entity_id, "period": a.period,
          "currency": currency, "entry_count": len(entries), "total_debits": str(total),
          "fx": fx_info, "entries": dumped}
  return reply("ok", data=data, warnings=warnings)
