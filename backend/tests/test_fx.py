"""Exchange rates from Open Exchange Rates: real month-end rates, stored once, a month locked only at its own rate.

No test here calls the real API: a fake transport answers like Open Exchange Rates does. The rates
the system starts with are the real ones, recorded once from the API into fixtures/fx.
"""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.fx import client as fx_client
from app.fx.client import FxError, fetch, parse
from app.fx.models import FxRate
from app.fx.store import RECORDED, day_rate, month_reference, store
from app.fx.update import fetch_final, update_daily
from app.world.drops import build_drop
from app.world.loader import rate_card_for
from helpers import AGENT, CONTROLLER

SECRET = "test-app-id-0123456789"
JUNE = json.loads((RECORDED / "2026-06-30.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def app_id(monkeypatch):
    monkeypatch.setenv("OPENEXCHANGERATES_API_KEY", SECRET)
    monkeypatch.delenv("OPENEXCHANGERATES_APP_ID", raising=False)


def answering(status: int, body, calls: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        assert request.url.params["app_id"] == SECRET
        payload = body if isinstance(body, str) else json.dumps(body)
        return httpx.Response(status, text=payload)

    return httpx.MockTransport(handler)


def raising(exc: Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


def day(rate_date: str, gbp: float = 0.75, eur: float = 0.87, timestamp: int = 1_790_000_000) -> dict:
    return {"disclaimer": "x", "license": "x", "timestamp": timestamp, "base": "USD", "rates": {"USD": 1, "GBP": gbp, "EUR": eur, "JPY": 150}}


# ---------------------------------------------------------------- the real recorded rates


def test_the_system_starts_with_the_real_month_end_rates(client):
    recorded = sorted(path.stem for path in RECORDED.glob("*.json"))
    assert recorded[0] == "2022-01-31" and recorded[-1] == "2026-09-30" and len(recorded) == 57
    with SessionLocal() as db:
        june = month_reference(db, "2026-06")
        assert june["rate_date"] == "2026-06-30"
        assert june["gbp_usd_reference"] == str((Decimal(1) / Decimal(str(JUNE["rates"]["GBP"]))).quantize(Decimal("0.000001")))
        assert june["eur_usd_reference"] == "1.141305"
        assert month_reference(db, "2026-10") is None  # October's last day has not come
    for path in RECORDED.glob("*.json"):
        assert "app_id" not in path.read_text(encoding="utf-8")


def test_the_reference_comes_from_the_real_rate_not_the_drop(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    drop = build_drop("2026-07", closed=False)
    drop["rate_card"]["gbp_usd_reference"] = "9.999999"
    assert client.post("/world/drops", headers=CONTROLLER, json=drop).status_code == 200
    with SessionLocal() as db:
        card = rate_card_for(db, "2026-07")
        july = json.loads((RECORDED / "2026-07-31.json").read_text(encoding="utf-8"))["rates"]
        assert card["gbp_usd_reference"] == str((Decimal(1) / Decimal(str(july["GBP"]))).quantize(Decimal("0.000001")))
        assert card["rate_date"] == "2026-07-31"
        assert card["gbp_usd_reporting"] == drop["rate_card"]["gbp_usd_reporting"]  # the management pack stays the drop's
    rates = client.get("/fx/rates?period=2026-07", headers=AGENT).json()
    assert rates["month_end"] == "2026-07-31" and rates["final_rate_stored"] is True


# ---------------------------------------------------------------- locking a month


def test_a_month_locks_only_at_its_own_final_month_end_rate(client):
    assert client.post("/entities/US/periods/2026-11", headers=CONTROLLER).status_code == 201
    refused = client.post("/entities/US/periods/2026-11/lock", headers=CONTROLLER)
    assert refused.status_code == 409 and refused.json()["error"] == "rate_missing"
    assert "2026-11-30" in refused.json()["message"]
    with SessionLocal() as db:
        store(db, parse("2026-11-30", json.dumps(day("2026-11-30"))), final=False, source="openexchangerates (latest)")
        db.commit()
    still = client.post("/entities/US/periods/2026-11/lock", headers=CONTROLLER)
    assert still.status_code == 409, "an intraday rate must not lock a month"
    with SessionLocal() as db:
        store(db, parse("2026-11-30", json.dumps(day("2026-11-30", gbp=0.76))), final=True, source="openexchangerates")
        db.commit()
    assert client.post("/entities/US/periods/2026-11/lock", headers=CONTROLLER).json()["status"] == "locked"


# ---------------------------------------------------------------- fetching


def test_a_past_day_is_fetched_once_and_stored_once(client):
    calls: list = []
    transport = answering(200, day("2026-10-21"), calls)
    with SessionLocal() as db:
        assert fetch_final(db, "2026-10-21", transport=transport) == ("2026-10-21", "stored")
        assert fetch_final(db, "2026-10-21", transport=transport) == ("2026-10-21", "already stored")
        assert len(calls) == 1
        assert calls[0].url.path == "/api/historical/2026-10-21.json"
        row = day_rate(db, "2026-10-21")
        assert row.final is True and json.loads(row.rates_json) == {"EUR": "0.87", "GBP": "0.75", "USD": "1"}
        with pytest.raises(FxError, match="not over yet"):
            fetch_final(db, date.today().isoformat(), transport=transport)


def test_the_daily_update_stores_yesterday_final_and_today_provisional(client):
    with SessionLocal() as db:
        result = update_daily(db, today=date(2026, 10, 23), transport=answering(200, day("x")))
        assert result == {"yesterday": "stored", "today": "stored"}
        rows = {(row.rate_date, row.final) for row in db.scalars(select(FxRate).where(FxRate.rate_date >= "2026-10-22"))}
        assert ("2026-10-22", True) in rows and ("2026-10-23", False) in rows


@pytest.mark.parametrize(
    "transport, expected",
    [
        (answering(401, {"error": True, "status": 401, "message": "invalid_app_id"}), "the app id was refused"),
        (answering(403, {"error": True, "status": 403, "message": "access_restricted"}), "monthly request quota is used up"),
        (answering(400, {"error": True, "status": 400, "message": "invalid_date"}), "not valid or not available yet"),
        (answering(200, "<html>maintenance</html>"), "the response is not JSON"),
        (answering(200, {"base": "EUR", "timestamp": 1, "rates": {"USD": 1}}), "unexpected response"),
        (raising(httpx.ConnectError("boom")), "could not reach Open Exchange Rates"),
        (raising(httpx.ReadTimeout("slow")), "did not answer in 20 seconds"),
    ],
    ids=["bad app id", "quota", "bad date", "not json", "wrong base", "network", "timeout"],
)
def test_every_failure_says_what_failed_and_never_shows_the_app_id(transport, expected):
    with pytest.raises(FxError) as error:
        fetch("2026-10-21", transport=transport)
    assert expected in str(error.value)
    assert "2026-10-21" in str(error.value)
    assert SECRET not in str(error.value)


def test_without_an_app_id_nothing_is_fetched(monkeypatch):
    monkeypatch.delenv("OPENEXCHANGERATES_API_KEY")
    monkeypatch.setattr(fx_client, "oxr_app_id", lambda: None)
    with pytest.raises(FxError, match="No Open Exchange Rates app id"):
        fetch("2026-10-21", transport=answering(200, day("x")))


def test_updating_rates_is_the_controllers(client, monkeypatch):
    from app.fx import update

    monkeypatch.setattr(update, "fetch", lambda rate_date, latest=False, transport=None: parse(rate_date, json.dumps(day(rate_date))))
    assert client.post("/fx/update", headers=AGENT).status_code == 403
    response = client.post("/fx/update", headers=CONTROLLER)
    assert response.status_code == 200 and set(response.json()) == {"yesterday", "today"}


def test_the_recorded_rates_are_the_providers_own_responses():
    for path in sorted(RECORDED.glob("*.json"))[:3] + [RECORDED / "2026-06-30.json"]:
        body = json.loads(path.read_text(encoding="utf-8"))
        assert set(body) == {"disclaimer", "license", "timestamp", "base", "rates"} and body["base"] == "USD"
        assert len(body["rates"]) > 100
        # The provider published it on that day (UTC), at its close: an end-of-day rate for that date.
        assert datetime.fromtimestamp(body["timestamp"], timezone.utc).date().isoformat() == path.stem
