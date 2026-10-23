"""The stored rates and the month-end rule.

A month's reference rate is the end-of-day rate for its last calendar day, because Northlight
pays salaries at month end. Every reader of a reference rate in Vestly comes through here.
Rates are stored as published (units per 1 USD) and turned into USD-per-unit reference rates
(gbp_usd_reference, eur_usd_reference) only when read.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import doing
from app.fx.client import FxResponse, parse
from app.fx.models import FxRate
from app.periods import month_of

RATE_PLACES = Decimal("0.000001")
RECORDED = Path(__file__).resolve().parents[2] / "fixtures" / "fx"
# Parsed recorded responses, keyed by file and modification time: every new database seeds from these.
_PARSED: dict[tuple[str, int], FxResponse] = {}


def month_end(period: str) -> str:
    return month_of(period).end


def store(session: Session, response: FxResponse, *, final: bool, source: str) -> FxRate:
    """Keep one response once. The same response again is not a second row."""
    digest = response.sha256
    existing = session.scalar(select(FxRate).where(FxRate.rate_date == response.rate_date, FxRate.response_sha256 == digest))
    if existing is not None:
        return existing
    row = FxRate(
        rate_date=response.rate_date,
        base=response.base,
        rates_json=json.dumps(response.rates, sort_keys=True),
        provider_timestamp=response.provider_timestamp,
        final=final,
        source=source,
        response_sha256=digest,
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(row)
    with doing("storing exchange rates", date=response.rate_date):
        session.flush()
    return row


def day_rate(session: Session, rate_date: str, *, final_only: bool = True) -> FxRate | None:
    query = select(FxRate).where(FxRate.rate_date == rate_date)
    if final_only:
        query = query.where(FxRate.final.is_(True))
    return session.scalar(query.order_by(FxRate.provider_timestamp.desc()).limit(1))


def latest_before(session: Session, rate_date: str) -> FxRate | None:
    """The most recent stored rate (final or not) dated before a day: the fallback, always named."""
    return session.scalar(
        select(FxRate).where(FxRate.rate_date < rate_date).order_by(FxRate.rate_date.desc(), FxRate.provider_timestamp.desc()).limit(1)
    )


def reference(row: FxRate) -> dict[str, str]:
    """USD per unit, the way the rest of Vestly reads rates: gbp_usd_reference, eur_usd_reference."""
    rates = json.loads(row.rates_json)
    card = {"rate_date": row.rate_date, "rate_source": row.source}
    for code in ("GBP", "EUR"):
        card[f"{code.lower()}_usd_reference"] = str((Decimal(1) / Decimal(rates[code])).quantize(RATE_PLACES))
    return card


def month_reference(session: Session, period: str) -> dict[str, str] | None:
    """The reference rates for a month: the final rate for its last day, or None if that is not stored yet."""
    row = day_rate(session, month_end(period))
    return None if row is None else reference(row)


def load_recorded(session: Session, through: str | None = None) -> list[FxRate]:
    """Store the recorded Open Exchange Rates responses in fixtures/fx, up to a date. They are real rates,
    fetched once with the app id and kept so tests and the demo run offline."""
    rows = []
    for path in sorted(RECORDED.glob("*.json")):
        rate_date = path.stem
        if through is not None and rate_date > through:
            continue
        key = (path.name, path.stat().st_mtime_ns)
        if key not in _PARSED:
            with doing("loading recorded exchange rates", file=path.name):
                _PARSED[key] = parse(rate_date, path.read_text(encoding="utf-8"))
        response = _PARSED[key]
        # Only end-of-day historical responses are ever recorded, so every recorded rate is final.
        rows.append(store(session, response, final=True, source="openexchangerates (recorded)"))
    return rows
