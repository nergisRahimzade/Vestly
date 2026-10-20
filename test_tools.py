import json
import sqlite3
from tools import call_tool

def show(title, result):
    print(f"--- {title}")
    print(json.dumps(result, indent=2, default=str)[:700])
    print()

show("1. get_employee", call_tool("get_employee", {"employee_id": 1001}))
show("2. list DE (3 rows)", call_tool("list_employees_by_entity", {"entity_id": "DE", "limit": 3}))
show("3. get_grant", call_tool("get_grant", {"employee_id": 1001}))
show("4. get_fx_rate", call_tool("get_fx_rate", {"currency": "EUR"}))

# Things that should FAIL politely
show("5. employee that doesn't exist", call_tool("get_employee", {"employee_id": 999999}))
show("6. invalid entity", call_tool("list_employees_by_entity", {"entity_id": "FR"}))
show("7. invented extra input", call_tool("get_employee", {"employee_id": 1001, "secret": True}))
show("8. tool that doesn't exist", call_tool("get_answer_key", {}))

# The diary
con = sqlite3.connect("company.db")
print("audit_log rows:", con.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
try:
    con.execute("DELETE FROM audit_log")
except sqlite3.DatabaseError as e:
    print("Tried to erase the diary ->", e)