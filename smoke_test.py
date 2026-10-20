from datetime import date
from decimal import Decimal
import sqlite3

from calculator import (
    shares_vested, equity_expense_for_period, proration_fraction,
    calculate_us_payroll, calculate_uk_payroll, calculate_germany_payroll,
)
from journal import payroll_entry, equity_expense_entry, forfeiture_reversal_entry

AS_OF = date(2026, 9, 30)
PERIOD = "2026-09"
D = Decimal

# ---------------- PART 1: known answers worked out by hand ----------------
us = calculate_us_payroll(120_000)           # 10,000 - (620+145+2,000) = 7,235
assert us["gross"] == D("10000.00") and us["net"] == D("7235.00")
assert us["employer_taxes"] == D("765.00")   # 620 + 145

uk = calculate_uk_payroll(60_000)            # 5,000 - 1,222.50 = 3,777.50
assert uk["net"] == D("3777.50")

de = calculate_germany_payroll(72_000)       # 6,000 - 4,023 = 1,977
assert de["net"] == D("1977.00")

# UK rounding case: 103,800/12 = 8,650; 692.00 + 125.43 + 1,297.50 = 2,114.93
uk2 = calculate_uk_payroll(103_800)
assert uk2["withholding"] == D("2114.93") and uk2["net"] == D("6535.07")
assert uk2["net"] + uk2["withholding"] == uk2["gross"]

# Entries balance, including employer taxes: debits 10,000 + 765 = credits 7,235 + 2,765 + 765
pe = payroll_entry("US", "USD", PERIOD, us["gross"], us["employer_taxes"], us["withholding"], us["net"])
assert sum(l.debit for l in pe.lines) == sum(l.credit for l in pe.lines) == D("10765.00")

# Vesting: cliff, full vest, whole shares only (1000 * 14/48 = 291.67 -> 291)
assert shares_vested(4800, date(2025, 9, 30), date(2026, 8, 31)) == 0
assert shares_vested(4800, date(2025, 9, 30), date(2026, 9, 30)) == 1200
assert shares_vested(4800, date(2025, 9, 30), date(2029, 9, 30)) == 4800
assert shares_vested(1000, date(2025, 1, 15), date(2026, 3, 1)) == 291

# Expense window: grant 2022-02-21, 4,800 options at 2.50 = 250.00/month for 48 months
G = date(2022, 2, 21)
def exp(period_end, term=None):
    return equity_expense_for_period(4800, 2.50, G, period_end, termination_date=term)
assert exp(date(2022, 2, 28))["expense"] == D("0.00")      # grant month: nothing yet
assert exp(date(2022, 3, 31))["expense"] == D("250.00")    # first month
assert exp(date(2026, 2, 28))["expense"] == D("250.00")    # 48th month
assert exp(date(2026, 3, 31))["expense"] == D("0.00")      # fully vested: stops

# Leaving before the cliff reverses everything booked so far (6 x 250 = 1,500)
r = exp(date(2022, 8, 31), term=date(2022, 8, 10))
assert r["expense"] == D("250.00") and r["reversal"] == D("1500.00")
assert exp(date(2022, 9, 30), term=date(2022, 8, 10))["expense"] == D("0.00")   # nothing after
# Leaving after the cliff reverses nothing
assert exp(date(2023, 6, 30), term=date(2023, 6, 10))["reversal"] == D("0.00")
fe = forfeiture_reversal_entry("US", "USD", "2022-08", r["reversal"])
assert fe.lines[0].debit == D("1500.00")

# Proration: September has 30 days
assert proration_fraction(date(2026, 9, 16), None, 2026, 9) == D("0.5")          # hired the 16th: 15/30
assert proration_fraction(date(2024, 1, 1), date(2026, 9, 10), 2026, 9) == D(10) / D(30)
assert proration_fraction(date(2026, 10, 1), None, 2026, 9) == 0                 # not hired yet
assert calculate_us_payroll(120_000, D("0.5"))["gross"] == D("5000.00")

print("PART 1 passed: all hand-worked checks\n")

# ---------------- PART 2: real database, showing the work ----------------
con = sqlite3.connect("company.db")
con.row_factory = sqlite3.Row
CALC = {"US": calculate_us_payroll, "UK": calculate_uk_payroll, "DE": calculate_germany_payroll}

def parse(s):
    return date.fromisoformat(s) if s else None

for entity in ["US", "UK", "DE"]:
    emp = con.execute("""
        SELECT e.employee_id, e.department, e.hire_date, c.annual_salary, c.currency
        FROM employees e JOIN compensation c USING (employee_id)
        WHERE e.entity_id = ? AND e.termination_date IS NULL
        ORDER BY e.employee_id LIMIT 1
    """, (entity,)).fetchone()
    frac = proration_fraction(parse(emp["hire_date"]), None, 2026, 9)
    p = CALC[entity](emp["annual_salary"], frac)
    print(f"{entity} employee {emp['employee_id']} ({emp['department']}), worked {frac:.4f} of month")
    print(f"  salary {emp['annual_salary']:,.2f} {emp['currency']} -> gross {p['gross']}")
    print(f"  withheld {p['withholding']}, net {p['net']}, employer taxes {p['employer_taxes']}")
    entry = payroll_entry(entity, emp["currency"], PERIOD, p["gross"], p["employer_taxes"], p["withholding"], p["net"])
    print(f"  entry balanced ({len(entry.lines)} lines)\n")

def pick_grant(where):
    return con.execute(f"""
        SELECT g.*, e.entity_id, en.currency
        FROM grants g JOIN employees e USING (employee_id)
        JOIN entities en ON en.entity_id = e.entity_id
        WHERE e.termination_date IS NULL AND {where}
        ORDER BY g.grant_id LIMIT 1
    """).fetchone()

# a grant that is mid-vest: expense should be booked
g = pick_grant("g.grant_date > '2022-09-30' AND g.grant_date <= '2025-09-30'")
res = equity_expense_for_period(g["options_granted"], g["fair_value_per_option"], parse(g["grant_date"]),
                                AS_OF, g["vest_years"], g["cliff_months"])
print(f"Mid-vest grant {g['grant_id']}: {g['options_granted']:,} options x {g['fair_value_per_option']} "
      f"/ {g['vest_years']*12}, granted {g['grant_date']}")
print(f"  vested: {shares_vested(g['options_granted'], parse(g['grant_date']), AS_OF, g['vest_years'], g['cliff_months'])}")
print(f"  expense for {PERIOD}: {res['expense']} {g['currency']}")
assert res["expense"] > 0
equity_expense_entry(g["entity_id"], g["currency"], PERIOD, res["expense"])

# a fully vested grant: expense must be zero
old = pick_grant("g.grant_date <= '2022-09-30'")
if old:
    res_old = equity_expense_for_period(old["options_granted"], old["fair_value_per_option"],
                                        parse(old["grant_date"]), AS_OF, old["vest_years"], old["cliff_months"])
    print(f"Fully vested grant {old['grant_id']}: expense for {PERIOD} = {res_old['expense']}")
    assert res_old["expense"] == 0

# ---------------- PART 3: every employee ----------------
rows = con.execute("""
    SELECT e.employee_id, e.entity_id, e.hire_date, e.termination_date, c.annual_salary, c.currency
    FROM employees e JOIN compensation c USING (employee_id)
""").fetchall()
done = skipped = 0
for r in rows:
    frac = proration_fraction(parse(r["hire_date"]), parse(r["termination_date"]), 2026, 9)
    if frac == 0:
        skipped += 1
        continue
    p = CALC[r["entity_id"]](r["annual_salary"], frac)
    assert p["net"] + p["withholding"] == p["gross"]
    payroll_entry(r["entity_id"], r["currency"], PERIOD, p["gross"], p["employer_taxes"], p["withholding"], p["net"])
    done += 1
print(f"\nPART 3: {done} payroll entries balanced, {skipped} people skipped (not employed in {PERIOD})")

print("\nALL CHECKS PASSED")