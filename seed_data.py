# seed_data.py
import random, sqlite3
from datetime import date, timedelta
from faker import Faker

random.seed(42)
Faker.seed(42)

AS_OF = date(2026, 9, 30)          # fixed "today" so output is identical every run
DB = "company.db"

ENTITIES = [("US", "Vestly Inc.", "USD"),
            ("UK", "Vestly Ltd.", "GBP"),
            ("DE", "Vestly GmbH", "EUR")]
HEADCOUNT = {"US": 165, "UK": 75, "DE": 60}             # ~55/25/20, 300 total
LOCALE = {"US": "en_US", "UK": "en_GB", "DE": "de_DE"}        # rough local-currency scaling

# department -> title -> (low, high) annual salary in USD terms
DEPTS = {
  "Engineering": {"Software Engineer": (110_000, 150_000),
                  "Senior Engineer": (140_000, 185_000),
                  "Staff Engineer": (180_000, 230_000)},
  "Sales":       {"Account Executive": (70_000, 110_000),
                  "Sales Manager": (110_000, 150_000)},
  "Marketing":   {"Marketing Specialist": (65_000, 90_000),
                  "Marketing Manager": (95_000, 130_000)},
  "Finance":     {"Accountant": (70_000, 100_000),
                  "Financial Analyst": (80_000, 125_000)},
  "People":      {"Recruiter": (60_000, 85_000),
                  "HR Partner": (80_000, 110_000)},
  "Operations":  {"Operations Associate": (55_000, 80_000),
                  "Operations Manager": (80_000, 105_000)},
}
FX_FACTOR = {"US": 1.0, "UK": 0.5, "DE": 0.55}

# Strike price steps over time (stand-in for 409A valuations)
STRIKE_STEPS = [(date(2022, 1, 1), 1.20), (date(2023, 6, 1), 2.10),
                (date(2024, 6, 1), 3.40), (date(2025, 6, 1), 5.00)]
def strike_on(d):
  price = STRIKE_STEPS[0][1]
  for start, p in STRIKE_STEPS:
      if d >= start:
          price = p
  return price

SCHEMA = """
DROP TABLE IF EXISTS entities;  DROP TABLE IF EXISTS employees;
DROP TABLE IF EXISTS compensation; DROP TABLE IF EXISTS grants;
DROP TABLE IF EXISTS users; DROP TABLE IF EXISTS answer_key;
CREATE TABLE entities (entity_id TEXT PRIMARY KEY, legal_name TEXT, currency TEXT);
CREATE TABLE employees (
  employee_id INTEGER PRIMARY KEY, entity_id TEXT, full_name TEXT, department TEXT,
  title TEXT, hire_date TEXT, termination_date TEXT,
  FOREIGN KEY (entity_id) REFERENCES entities(entity_id));
CREATE TABLE compensation (
  employee_id INTEGER, annual_salary REAL, currency TEXT, effective_date TEXT);
CREATE TABLE grants (
  grant_id INTEGER PRIMARY KEY, employee_id INTEGER, grant_date TEXT,
  options_granted INTEGER, strike_price REAL, fair_value_per_option REAL,
  vest_years INTEGER, cliff_months INTEGER);
CREATE TABLE users (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT);
-- The agent must NEVER be given access to this table. Tests only.
CREATE TABLE answer_key (
  id INTEGER PRIMARY KEY, employee_id INTEGER, anomaly_type TEXT, description TEXT);
"""

def main():
  con = sqlite3.connect(DB)
  con.executescript(SCHEMA)
  con.executemany("INSERT INTO entities VALUES (?,?,?)", ENTITIES)
  con.executemany("INSERT INTO users (name, role) VALUES (?,?)", [
      ("Dana Whitfield", "payroll_preparer"),
      ("Marcus Lee", "controller"),
      ("Priya Raman", "cfo"),
  ])

  currency = {e[0]: e[2] for e in ENTITIES}
  emp_id = 1000
  grant_id = 1
  for ent, count in HEADCOUNT.items():
      fake = Faker(LOCALE[ent])
      for _ in range(count):
          emp_id += 1
          dept = random.choice(list(DEPTS))
          title = random.choice(list(DEPTS[dept]))
          lo, hi = DEPTS[dept][title]
          hire = date(2022, 1, 1) + timedelta(
              days=random.randint(0, (AS_OF - date(2022, 1, 1)).days))
          term = None
          if random.random() < 0.08 and (AS_OF - hire).days > 180:
              term = hire + timedelta(days=random.randint(180, (AS_OF - hire).days))
          salary = round(random.uniform(lo, hi) * FX_FACTOR[ent], -2)

          con.execute("INSERT INTO employees VALUES (?,?,?,?,?,?,?)",
              (emp_id, ent, fake.name(), dept, title, hire.isoformat(),
                term.isoformat() if term else None))
          con.execute("INSERT INTO compensation VALUES (?,?,?,?)",
              (emp_id, salary, currency[ent], hire.isoformat()))

          if random.random() < 0.70:   # ~70% get grants
              strike = strike_on(hire)
              con.execute("INSERT INTO grants VALUES (?,?,?,?,?,?,?,?)",
                  (grant_id, emp_id, hire.isoformat(),
                    random.choice([2_000, 5_000, 10_000, 20_000, 40_000]),
                    strike, round(strike * random.uniform(0.45, 0.60), 2), 4, 12))
              grant_id += 1
  con.commit()
  con.close()
  print("Seeded", emp_id - 1000, "employees")

if __name__ == "__main__":
    main()