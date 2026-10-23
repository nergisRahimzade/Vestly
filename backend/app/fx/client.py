"""Open Exchange Rates over HTTP. USD base (the free plan's only base); GBP and EUR go through USD.

Every failure becomes an FxError that says what failed in plain words. The app id travels as
a query parameter, so no message here ever contains the request URL.
"""

import hashlib
import json
from dataclasses import dataclass

import httpx

from app.config import oxr_app_id

API = "https://openexchangerates.org/api"
TIMEOUT_SECONDS = 20.0
NEEDED = ("USD", "GBP", "EUR")


class FxError(Exception):
    """Rates could not be fetched. The message names the date and the reason, never the app id."""


@dataclass(frozen=True)
class FxResponse:
    rate_date: str
    base: str
    rates: dict[str, str]
    provider_timestamp: int
    raw: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw.encode("utf-8")).hexdigest()


def parse(rate_date: str, raw: str) -> FxResponse:
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FxError(f"Rates for {rate_date}: the response is not JSON ({exc.msg} at character {exc.pos}).") from exc
    if body.get("error"):
        raise FxError(f"Rates for {rate_date}: {_explain(body.get('status'), body.get('message'), body.get('description'))}")
    missing = [code for code in NEEDED if code not in body.get("rates", {})]
    if body.get("base") != "USD" or missing or "timestamp" not in body:
        raise FxError(f"Rates for {rate_date}: unexpected response (base {body.get('base')!r}, missing {missing or 'timestamp'}).")
    return FxResponse(
        rate_date=rate_date,
        base=body["base"],
        rates={code: str(body["rates"][code]) for code in NEEDED},
        provider_timestamp=int(body["timestamp"]),
        raw=raw,
    )


def fetch(rate_date: str, *, latest: bool = False, transport: httpx.BaseTransport | None = None) -> FxResponse:
    """The end-of-day rates for a past date, or the latest intraday rates (latest=True)."""
    app_id = oxr_app_id()
    if not app_id:
        raise FxError("No Open Exchange Rates app id: set OPENEXCHANGERATES_API_KEY in .env or the environment.")
    path = "latest.json" if latest else f"historical/{rate_date}.json"
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport) as client:
            response = client.get(f"{API}/{path}", params={"app_id": app_id})
    except httpx.TimeoutException as exc:
        raise FxError(f"Rates for {rate_date}: Open Exchange Rates did not answer in {TIMEOUT_SECONDS:.0f} seconds.") from exc
    except httpx.HTTPError as exc:
        raise FxError(f"Rates for {rate_date}: could not reach Open Exchange Rates ({type(exc).__name__}).") from exc
    if response.status_code != 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise FxError(f"Rates for {rate_date}: {_explain(response.status_code, body.get('message'), body.get('description'))}")
    return parse(rate_date, response.text)


def _explain(status, message, description) -> str:
    known = {
        "invalid_app_id": "the app id was refused (check OPENEXCHANGERATES_API_KEY)",
        "missing_app_id": "no app id was sent",
        "not_allowed": "this plan may not make that request",
        "access_restricted": "access is restricted, usually because the monthly request quota is used up",
        "not_found": "no rates exist for that date",
        "invalid_date": "the date is not valid or not available yet",
        "invalid_base": "the base currency is not allowed on this plan",
    }
    reason = known.get(message, message or "unknown error")
    return f"Open Exchange Rates answered {status}: {reason}." + (f" ({description})" if description and message not in known else "")
