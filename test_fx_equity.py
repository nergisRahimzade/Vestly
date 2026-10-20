import sqlite3
from datetime import date
from decimal import Decimal
from calculator import equity_expense_for_period
from tools import call_tool

PERIOD, PERIOD_END = "2026-09", date(2026, 9, 30)

def parse(s):
    return date.fromisoformat(s) if s else None

# 1. US books are in USD: the rate must be exactly 1
us = call_tool("generate_journal_entries", {"entity_id": "US", "period": PERIOD})
assert us["status"] == "ok", us
assert us["data"]["fx"]["rate"] == "1"
print("US: rate 1, no conversion. Entries:", us["data"]["entry_count"])

# 2. Germany: converted at a real rate
de = call_tool("generate_journal_entries", {"entity_id": "DE", "period": PERIOD})
print("DE status:", de["status"], "| warnings:", de["warnings"])
assert de["status"] == "ok", de
fx_info = de["data"]["fx"]
rate = Decimal(fx_info["rate"])
print(f"USD -> EUR rate used: {rate} (saved at {fx_info['fetched_at']})")

# 3. Hand-check: work out the USD expense independently, multiply by the rate, compare
con = sqlite3.connect("company.db")
con.row_factory = sqlite3.Row
rows = con.execute("""SELECT g.*, e.termination_date FROM grants g
                      JOIN employees e USING (employee_id) WHERE e.entity_id = 'DE'""").fetchall()
usd_total = Decimal("0.00")
for g in rows:
    r = equity_expense_for_period(g["options_granted"], g["fair_value_per_option"],
                                  parse(g["grant_date"]), PERIOD_END, g["vest_years"],
                                  g["cliff_months"], termination_date=parse(g["termination_date"]))
    usd_total += r["expense"]

eur_in_entries = sum(Decimal(l["debit"]) for e in de["data"]["entries"]
                     if e["source"] == "equity_expense" for l in e["lines"]
                     if l["account"] == "Stock-Based Compensation Expense")
expected = (usd_total * rate).quantize(Decimal("0.01"))
print(f"USD expense {usd_total} x {rate} = {expected} EUR  |  entries total {eur_in_entries} EUR")
assert abs(eur_in_entries - expected) <= Decimal("0.06")   # at most a cent of rounding per department

# 4. Same input, same fingerprint (as long as no new rate was saved in between)
de2 = call_tool("generate_journal_entries", {"entity_id": "DE", "period": PERIOD})
print("fingerprint stable:", de["data"]["fingerprint"] == de2["data"]["fingerprint"])

# 5. UK too
uk = call_tool("generate_journal_entries", {"entity_id": "UK", "period": PERIOD})
assert uk["status"] == "ok", uk
print("UK: USD -> GBP rate", uk["data"]["fx"]["rate"], "| entries:", uk["data"]["entry_count"])

print("\nFX EQUITY CHECKS PASSED")