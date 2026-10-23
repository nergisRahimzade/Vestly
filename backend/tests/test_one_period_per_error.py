"""An error is booked once, in one period. Later closes see it on file and leave it alone."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth.identities import Actor
from app.db import ENGINE, SessionLocal
from app.ledger.errors import LedgerError
from app.ledger.service import draft_entry
from app.world.drops import build_drop
from app.world.loader import store_drop
from helpers import AGENT, CONTROLLER

PRIYA_MARCH = "payroll:overpayment:NL-US-003:2026-03"


def _open_april_and_may(client) -> None:
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    with SessionLocal() as session:
        for period in ("2026-04", "2026-05"):
            store_drop(session, build_drop(period, closed=False))
        session.commit()
    for period in ("2026-04", "2026-05"):
        for entity in ("US", "UK", "DE"):
            assert client.post(f"/entities/{entity}/periods/{period}", headers=CONTROLLER).status_code == 201


def _overpayments(body: dict) -> list[dict]:
    return [entry for entry in body["journals"] if entry["booking_key"] == PRIYA_MARCH]


def test_priyas_march_overpayment_is_drafted_once(client):
    _open_april_and_may(client)

    april = client.post("/payroll/drafts?period=2026-04", headers=AGENT)
    assert april.status_code == 200, april.text
    drafted = _overpayments(april.json())
    assert len(drafted) == 1
    assert drafted[0]["total_debit"] == "1500.00"
    assert any(note["kind"] == "overpayment" for note in april.json()["notes"])

    for period in ("2026-05", "2026-06"):
        later = client.post(f"/payroll/drafts?period={period}", headers=AGENT)
        assert later.status_code == 200, later.text
        body = later.json()
        assert _overpayments(body) == []
        assert not any(note["kind"] == "overpayment" for note in body["notes"])
        on_file = next(row for row in body["already_on_file"] if row["booking_key"] == PRIYA_MARCH)
        assert on_file["journal"] == drafted[0]["number"]
        assert on_file["period"] == "2026-04"

    receivables = [
        entry
        for period in ("2026-04", "2026-05", "2026-06")
        for entry in client.get(f"/entities/US/journal-entries?period={period}", headers=CONTROLLER).json()
        if entry["booking_key"] == PRIYA_MARCH
    ]
    assert len(receivables) == 1


def test_wages_stay_in_their_own_month(client):
    """Each month books its own items. The only planned link is a UK accrual reversed the next month."""
    _open_april_and_may(client)
    previous = {"2026-04": "2026-03", "2026-05": "2026-04", "2026-06": "2026-05"}
    for period in ("2026-04", "2026-05", "2026-06"):
        body = client.post(f"/payroll/drafts?period={period}", headers=AGENT).json()
        keys = {entry["booking_key"] for entry in body["journals"]}
        assert f"payroll:wages:US:{period}" in keys
        for key in keys - {PRIYA_MARCH}:
            if key.startswith("payroll:uk_accrual_reversal:"):
                assert key.endswith(previous[period])
            else:
                assert key.endswith(period), key


def test_a_rejected_correction_can_be_drafted_again(client):
    _open_april_and_may(client)
    april = client.post("/payroll/drafts?period=2026-04", headers=AGENT).json()
    entry = _overpayments(april)[0]
    rejected = client.post(
        f"/journal-entries/{entry['id']}/reject",
        headers=CONTROLLER,
        json={"note": "Wait for HR to confirm the last day."},
    )
    assert rejected.status_code == 200, rejected.text

    may = client.post("/payroll/drafts?period=2026-05", headers=AGENT).json()
    assert len(_overpayments(may)) == 1


def test_the_service_and_the_database_refuse_a_second_live_key(client):
    _open_april_and_may(client)
    payload = {
        "entity_code": "US",
        "period": "2026-04",
        "memo": "Test entry",
        "booking_key": "test:once",
        "lines": [
            {"account_code": "1200", "debit": "10.00", "credit": "0.00", "department": "Product"},
            {"account_code": "5000", "debit": "0.00", "credit": "10.00", "department": "Product"},
        ],
    }
    first = client.post("/journal-entries", headers=AGENT, json=payload)
    assert first.status_code == 201, first.text
    again = client.post("/journal-entries", headers=AGENT, json={**payload, "period": "2026-05"})
    assert again.status_code == 409
    assert again.json()["error"] == "already_booked"

    with SessionLocal() as session:
        with pytest.raises(LedgerError):
            draft_entry(session, {**payload, "period": "2026-05"}, Actor(handle="vestly", role="agent"))

    with ENGINE.begin() as connection:
        row = connection.execute(
            text("SELECT entity_id, period_id FROM journal_entries WHERE booking_key = 'test:once'")
        ).one()
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO journal_entries (id, entity_id, period_id, number, memo, booking_key, status, "
                    "currency, prepared_by, prepared_at) VALUES ('dup', :entity, :period, 'JE-DUP', 'dup', "
                    "'test:once', 'draft', 'USD', 'vestly', CURRENT_TIMESTAMP)"
                ),
                {"entity": row.entity_id, "period": row.period_id},
            )
