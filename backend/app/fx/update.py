"""Fetching rates: the month-end backfill and the daily update.

    python -m app.fx.update --month-ends 2022-01 2026-09 [--record]   one request per month-end
    python -m app.fx.update --daily [--record]                         yesterday's final rate, today's latest

The free plan allows 1,000 requests a month, so history is fetched one month-end at a time
(the rate that locks a month) and then one or two requests a day. --record also writes each
final response to fixtures/fx/<date>.json so tests and the demo run offline on real rates.
"""

import argparse
import sys
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.errors import doing
from app.fx.client import FxError, fetch
from app.fx.store import RECORDED, day_rate, month_end, store
from app.periods import add_months


def periods_between(first: str, last: str) -> list[str]:
    found, current = [], first
    while current <= last:
        found.append(current)
        current = add_months(f"{current}-01", 1)[:7]
    return found


def fetch_final(session: Session, rate_date: str, *, record: bool = False, transport=None) -> tuple[str, str]:
    """End-of-day rates for a day already over. Returns (date, "stored" | "already stored")."""
    if date.fromisoformat(rate_date) >= datetime.now(timezone.utc).date():
        raise FxError(f"Rates for {rate_date}: that day is not over yet, so there is no end-of-day rate.")
    if day_rate(session, rate_date) is not None:
        return rate_date, "already stored"
    response = fetch(rate_date, transport=transport)
    store(session, response, final=True, source="openexchangerates")
    if record:
        with doing("recording the response", date=rate_date):
            RECORDED.mkdir(parents=True, exist_ok=True)
            (RECORDED / f"{rate_date}.json").write_text(response.raw, encoding="utf-8")
    session.commit()
    return rate_date, "stored"


def backfill_month_ends(session: Session, first: str, last: str, *, record: bool = False, transport=None) -> list[tuple[str, str]]:
    results = []
    for period in periods_between(first, last):
        try:
            results.append(fetch_final(session, month_end(period), record=record, transport=transport))
        except FxError as exc:
            results.append((month_end(period), f"failed: {exc}"))
    return results


def update_daily(session: Session, *, today: date | None = None, record: bool = False, transport=None) -> dict:
    """Yesterday's end-of-day rate (final) and today's latest (provisional, never locks a month)."""
    today = today or datetime.now(timezone.utc).date()
    result = {}
    try:
        result["yesterday"] = fetch_final(session, (today - timedelta(days=1)).isoformat(), record=record, transport=transport)[1]
    except FxError as exc:
        result["yesterday"] = f"failed: {exc}"
    try:
        response = fetch(today.isoformat(), latest=True, transport=transport)
        store(session, response, final=False, source="openexchangerates (latest)")
        session.commit()
        result["today"] = "stored"
    except FxError as exc:
        result["today"] = f"failed: {exc}"
    return result


def main(argv: list[str] | None = None) -> int:
    from app.db import SessionLocal, init_db

    parser = argparse.ArgumentParser(description="Fetch exchange rates from Open Exchange Rates.")
    parser.add_argument("--month-ends", nargs=2, metavar=("FIRST", "LAST"), help="periods YYYY-MM, inclusive")
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--record", action="store_true", help="also write final responses to fixtures/fx")
    args = parser.parse_args(argv)
    init_db()
    failures = 0
    with SessionLocal() as session:
        if args.month_ends:
            for rate_date, status in backfill_month_ends(session, *args.month_ends, record=args.record):
                print(f"{rate_date}  {status}")
                failures += status.startswith("failed")
        if args.daily:
            for key, status in update_daily(session, record=args.record).items():
                print(f"{key}  {status}")
                failures += status.startswith("failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
