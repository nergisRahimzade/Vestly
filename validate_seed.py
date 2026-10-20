# validate_seed.py
import sqlite3, hashlib

con = sqlite3.connect("company.db")
q = lambda sql: con.execute(sql).fetchall()
SEEDED = ["entities", "employees", "compensation", "grants", "users", "answer_key"]

# Headcount and entity split
assert q("SELECT COUNT(*) FROM employees")[0][0] == 300
assert dict(q("SELECT entity_id, COUNT(*) FROM employees GROUP BY entity_id")) == \
       {"US": 165, "UK": 75, "DE": 60}

# Every employee has exactly one compensation row, in their entity's currency
assert q("""SELECT COUNT(*) FROM employees e
            LEFT JOIN compensation c ON c.employee_id = e.employee_id
            GROUP BY e.employee_id HAVING COUNT(c.employee_id) != 1""") == []
assert q("""SELECT COUNT(*) FROM employees e
            JOIN entities en ON en.entity_id = e.entity_id
            JOIN compensation c ON c.employee_id = e.employee_id
            WHERE c.currency != en.currency""")[0][0] == 0

# No orphan grants; grants never predate the hire
assert q("""SELECT COUNT(*) FROM grants g
            LEFT JOIN employees e ON e.employee_id = g.employee_id
            WHERE e.employee_id IS NULL""")[0][0] == 0
assert q("""SELECT COUNT(*) FROM grants g JOIN employees e USING (employee_id)
            WHERE g.grant_date < e.hire_date""")[0][0] == 0

# Terminations come after hires; salaries and grants are sane
assert q("""SELECT COUNT(*) FROM employees
            WHERE termination_date IS NOT NULL
              AND termination_date <= hire_date""")[0][0] == 0
assert q("SELECT COUNT(*) FROM compensation WHERE annual_salary <= 0")[0][0] == 0
assert q("""SELECT COUNT(*) FROM grants
            WHERE options_granted <= 0 OR strike_price <= 0""")[0][0] == 0

# Roughly 70% of employees have grants (allow a margin)
share = q("SELECT COUNT(DISTINCT employee_id) FROM grants")[0][0] / 300
assert 0.62 <= share <= 0.78, f"grant share {share:.2f}"

# Distinct user roles (needed for segregation of duties later)
assert len({r for (r,) in q("SELECT role FROM users")}) == 3

# Answer key should be empty until you plant anomalies
assert q("SELECT COUNT(*) FROM answer_key")[0][0] == 0

# Warning only: duplicate names can happen by chance (and you'll plant some on purpose)
dupes = q("SELECT full_name, COUNT(*) FROM employees GROUP BY full_name HAVING COUNT(*) > 1")
if dupes:
    print("Note: duplicate names:", dupes)

# Fingerprint of the seeded tables only (fx tables change hourly, so they're excluded)
h = hashlib.sha256()
for t in SEEDED:
    for row in q(f"SELECT * FROM {t} ORDER BY 1"):
        h.update(repr(row).encode())
print("All checks passed. Fingerprint:", h.hexdigest()[:16])