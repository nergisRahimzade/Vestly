# eyeball.py
import sqlite3

con = sqlite3.connect("company.db")

print("--- 15 random employees ---")
rows = con.execute("""
    SELECT e.entity_id, e.department, e.title, c.annual_salary, c.currency
    FROM employees e JOIN compensation c USING (employee_id)
    ORDER BY RANDOM() LIMIT 15
""").fetchall()
for r in rows:
    print(r)

print()
print("--- Average salary per country and department ---")
rows = con.execute("""
    SELECT e.entity_id, e.department, ROUND(AVG(c.annual_salary)) AS avg_salary, COUNT(*) AS people
    FROM employees e JOIN compensation c USING (employee_id)
    GROUP BY e.entity_id, e.department
""").fetchall()
for r in rows:
    print(r)