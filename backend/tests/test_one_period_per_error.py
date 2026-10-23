"""An error is booked once, in one period. Later closes see it on file and leave it alone.

January to May arrive closed, so June is the first month Vestly can book Priya's March
overpayment. July must not book it again.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth.identities import Actor
from app.db import ENGINE, SessionLocal
from app.ledger.errors import LedgerError
from app.ledger.service import draft_entry
from app.world.drops import build_drop
from helpers import AGENT, CONTROLLER

PRIYA_MARCH = "payroll:overpayment:NL-US-003:2026-03"


def _load_world_and_july(client) -> None:
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    july = client.post("/world/drops", headers=CONTROLLER, json=build_drop("2026-07", plants=[], closed=False))
    assert july.status_code == 200, july.text


def _overpayments(body: dict) -> list[dict]:
    return [entry for entry in body["journals"] if entry["booking_key"] == PRIYA_MARCH]


def test_priyas_march_overpayment_is_drafted_once(client):
    _load_world_and_july(client)

    june = client.post("/payroll/drafts?period=2026-06", headers=AGENT)
    assert june.status_code == 200, june.text
    drafted = _overpayments(june.json())
    assert len(drafted) == 1
    assert drafted[0]["total_debit"] == "1500.00"
    assert any(note["kind"] == "overpayment" for note in june.json()["notes"])

    july = client.post("/payroll/drafts?period=2026-07", headers=AGENT)
    assert july.status_code == 200, july.text
    body = july.json()
    assert _overpayments(body) == []
    assert not any(note["kind"] == "overpayment" for note in body["notes"])
    on_file = next(row for row in body["already_on_file"] if row["booking_key"] == PRIYA_MARCH)
    assert on_file["journal"] == drafted[0]["number"]
    assert on_file["period"] == "2026-06"

    receivables = [
        entry
        for period in ("2026-03", "2026-04", "2026-05", "2026-06", "2026-07")
        for entry in client.get(f"/entities/US/journal-entries?period={period}", headers=CONTROLLER).json()
        if entry["booking_key"] == PRIYA_MARCH
    ]
    assert len(receivables) == 1


def test_each_month_books_its_own_items(client):
    """The only planned link between months is a UK accrual reversed the next month."""
    _load_world_and_july(client)
    for period, previous in (("2026-06", "2026-05"), ("2026-07", "2026-06")):
        body = client.post(f"/payroll/drafts?period={period}", headers=AGENT).json()
        keys = {entry["booking_key"] for entry in body["journals"]}
        assert f"payroll:wages:US:{period}" in keys
        for key in keys - {PRIYA_MARCH}:
            if key.startswith("payroll:uk_accrual_reversal:"):
                assert key.endswith(previous)
            else:
                assert key.endswith(period), key


def test_a_rejected_correction_can_be_drafted_again(client):
    _load_world_and_july(client)
    june = client.post("/payroll/drafts?period=2026-06", headers=AGENT).json()
    entry = _overpayments(june)[0]
    rejected = client.post(
        f"/journal-entries/{entry['id']}/reject",
        headers=CONTROLLER,
        json={"note": "Wait for HR to confirm the last day."},
    )
    assert rejected.status_code == 200, rejected.text

    july = client.post("/payroll/drafts?period=2026-07", headers=AGENT).json()
    assert len(_overpayments(july)) == 1


def test_the_service_and_the_database_refuse_a_second_live_key(client):
    _load_world_and_july(client)
    payload = {
        "entity_code": "US",
        "period": "2026-06",
        "memo": "Test entry",
        "booking_key": "test:once",
        "lines": [
            {"account_code": "1200", "debit": "10.00", "credit": "0.00", "department": "Product"},
            {"account_code": "5000", "debit": "0.00", "credit": "10.00", "department": "Product"},
        ],
    }
    first = client.post("/journal-entries", headers=AGENT, json=payload)
    assert first.status_code == 201, first.text
    again = client.post("/journal-entries", headers=AGENT, json={**payload, "period": "2026-07"})
    assert again.status_code == 409
    assert again.json()["error"] == "already_booked"

    with SessionLocal() as session:
        with pytest.raises(LedgerError):
            draft_entry(session, {**payload, "period": "2026-07"}, Actor(handle="vestly.payroll", role="agent"))

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
