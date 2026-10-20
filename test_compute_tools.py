import json
import sqlite3
from decimal import Decimal
from tools import call_tool

def show(title, result, limit=900):
    print(f"--- {title}")
    print(json.dumps(result, indent=2, default=str)[:limit])
    print()

# 1. Payroll, then a hand-check of the totals
pay = call_tool("run_payroll", {"entity_id": "US", "period": "2026-09"})
show("1. run_payroll US 2026-09", pay)
t = pay["data"]["totals"]
assert Decimal(t["net"]) + Decimal(t["withholding"]) == Decimal(t["gross"])

# 2. Vesting
show("2. calculate_vesting 1001", call_tool("calculate_vesting", {"employee_id": 1001, "as_of": "2026-09-30"}))

# 3. Someone who left: vesting must stop
con = sqlite3.connect("company.db")
gone = con.execute("""SELECT e.employee_id FROM employees e JOIN grants g USING (employee_id)
                      WHERE e.termination_date IS NOT NULL ORDER BY e.employee_id LIMIT 1""").fetchone()
if gone:
    show("3. vesting for someone who left",
         call_tool("calculate_vesting", {"employee_id": gone[0], "as_of": "2026-09-30"}))

# 4. Journal entries: same input twice must give the same fingerprint
a = call_tool("generate_journal_entries", {"entity_id": "DE", "period": "2026-09"})
b = call_tool("generate_journal_entries", {"entity_id": "DE", "period": "2026-09"})
print("--- 4. generate_journal_entries DE 2026-09")
print("entries:", a["data"]["entry_count"], "| total debits:", a["data"]["total_debits"],
      "| fingerprint:", a["data"]["fingerprint"])
assert a["data"]["fingerprint"] == b["data"]["fingerprint"]
print("same input -> same fingerprint: OK\n")

# 5. Tie-out: salary debits in the entries must equal gross from run_payroll
de = call_tool("run_payroll", {"entity_id": "DE", "period": "2026-09"})
salary = sum(Decimal(l["debit"]) for e in a["data"]["entries"] if e["source"] == "payroll"
             for l in e["lines"] if l["account"] == "Salary Expense")
assert salary == Decimal(de["data"]["totals"]["gross"])
print("tie-out: salary debits == run_payroll gross:", salary, "\n")

# 6. Bad calls that must fail politely
show("6a. impossible month", call_tool("run_payroll", {"entity_id": "US", "period": "2026-13"}))
show("6b. agent tries to supply numbers",
     call_tool("generate_journal_entries", {"entity_id": "DE", "period": "2026-09", "gross": 999999}))
print("ALL COMPUTE TOOL CHECKS PASSED")