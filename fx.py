import sqlite3
from dotenv import dotenv_values
import time
import requests
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler

config = dotenv_values(".env")
DB_PATH = "company.db"
API_URL = "https://openexchangerates.org/api/latest.json"
API_KEY = config["OPEN_EXCHANGE_RATES_APP_ID"]
BASE = "USD"
CURRENCIES = ["USD", "GBP", "EUR"]
MAX_AGE_HOURS = 3

if not API_KEY:
    raise RuntimeError("OPEN_EXCHANGE_RATES_APP_ID is not set. Check your .env file.")

def init_db():
  with sqlite3.connect(DB_PATH) as db:
    db.execute("""
      CREATE TABLE IF NOT EXISTS fx_rates (
        fetched_at TEXT NOT NULL,
        base TEXT NOT NULL,
        currency TEXT NOT NULL,
        rate REAL NOT NULL
      )
    """)
    db.execute("""
      CREATE TABLE IF NOT EXISTS period_rates (
        period TEXT NOT NULL,
        currency TEXT NOT NULL,
        rate TEXT NOT NULL,
        source TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        PRIMARY KEY (period, currency)
      )
    """)

def fetch_rates():
  """Call the API and return {currency: rate} relative to BASE."""
  resp = requests.get(
    API_URL,
    params={"app_id": API_KEY, "base": BASE},  #parameter names depend on the provider
    timeout=10
  )
  resp.raise_for_status()
  data = resp.json()
  #Adjust this line to match your provider's response format:
  return {c: data["rates"][c] for c in CURRENCIES if c != BASE}

def update_rates():
  """Fetch rates and save a timestamped snapshot. Safe to call repeatedly."""
  try:
      rates = fetch_rates()
  except requests.HTTPError as e:
      print(f"[fx] update failed (HTTP {e.response.status_code}), keeping last known rates")
      return
  except Exception as e:
      print(f"[fx] update failed ({type(e).__name__}), keeping last known rates")
      return

  now = datetime.now(timezone.utc).isoformat()
  with sqlite3.connect(DB_PATH) as db:
      db.executemany(
          "INSERT INTO fx_rates VALUES (?, ?, ?, ?)",
          [(now, BASE, cur, rate) for cur, rate in rates.items()],
      )
  print(f"[fx] updated at {now}: {rates}")

def get_rate(currency, as_of=None):
  """Latest saved rate for BASE -> currency (or the lates before 'as_of)"""
  if currency == BASE:
    return 1.0, None
  query = "SELECT fetched_at, rate FROM fx_rates WHERE base=? AND currency=?"
  params = [BASE, currency]
  if as_of:
    query += " AND fetched_at <= ?"
    params.append(as_of)
  query += " ORDER BY fetched_at DESC LIMIT 1"
  with sqlite3.connect(DB_PATH) as db:
    row = db.execute(query, params).fetchone()
  if row is None:
    raise ValueError(f"No saved rate for {currency}")
  return row[1], row[0]

def get_period_rate(currency, period, period_end):
  """One fixed USD->currency rate per month. Returns (rate, source, warning)."""
  init_db()
  if currency == BASE:
    return 1.0, "base currency", None

  # 1. Already locked? Use it and stop.
  with sqlite3.connect(DB_PATH) as db:
    row = db.execute(
      "SELECT rate, source FROM period_rates WHERE period=? AND currency=?",
      (period, currency)).fetchone()
  if row:
    rate, source = row
    warning = f"{period} rate is a fallback ({source})" if source.startswith("fallback") else None
    return rate, source, warning

  # 2. Month not over yet? Latest rate, but do NOT lock it.
  if period_end >= datetime.now(timezone.utc).date():
    rate, fetched_at = get_rate(currency)
    return rate, f"open period, latest rate at {fetched_at}", f"{period} is not closed; rate not locked"

  # 3. Closed month: pick a rate once and lock it.
  as_of = datetime(period_end.year, period_end.month, period_end.day,
                   23, 59, 59, tzinfo=timezone.utc).isoformat()
  try:
    rate, fetched_at = get_rate(currency, as_of)
    source = f"closing rate saved at {fetched_at}"
  except ValueError:                      # nothing saved that early
    rate, fetched_at = get_rate(currency)
    source = f"fallback: latest rate at {fetched_at}"

  with sqlite3.connect(DB_PATH) as db:
    db.execute("INSERT OR IGNORE INTO period_rates VALUES (?, ?, ?, ?, ?)",
               (period, currency, rate, source, datetime.now(timezone.utc).isoformat()))

  warning = f"{period} rate is a fallback ({source})" if source.startswith("fallback") else None
  return rate, source, warning



def convert(amount, from_cur, to_cur, as_of=None):
  """Convert an amount between currencies using saved rates (via BASE)."""
  rate_from, ts_from = get_rate(from_cur, as_of)
  rate_to, ts_to = get_rate(to_cur, as_of)
  result = amount / rate_from * rate_to

  # Staleness warning
  warning = None
  newest = max(t for t in (ts_from, ts_to) if t) if (ts_from or ts_to) else None
  if newest:
      age = datetime.now(timezone.utc) - datetime.fromisoformat(newest)
      if age.total_seconds() > MAX_AGE_HOURS * 3600:
          warning = f"Rates are {age.total_seconds()/3600:.1f} hours old"

  return {
      "amount": round(result, 2),
      "rate_used": rate_to / rate_from,
      "rates_timestamp": newest,
      "warning": warning,
  }

def start_hourly_updates():
  init_db()
  update_rates()
  scheduler = BackgroundScheduler()
  scheduler.add_job(update_rates, "interval", hours=1)
  scheduler.start()
  return scheduler

if __name__ == "__main__":
  start_hourly_updates()
  print(convert(1000, "EUR", "USD"))
  while True:
    time.sleep(60)

