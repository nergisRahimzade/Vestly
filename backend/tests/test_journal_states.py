"""A journal moves draft -> approved or rejected -> posted, and the preparer never reviews it."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import ENGINE
from helpers import AGENT, CONTROLLER

WAGES = {
    "entity_code": "US",
    "period": "2026-06",
    "memo": "US Engineering wages for June 2026",
    "lines": [
        {"account_code": "5000", "debit": "250.00", "credit": "0.00", "department": "Engineering"},
        {"account_code": "2100", "debit": "0.00", "credit": "250.00"},
    ],
}


@pytest.fixture()
def draft(client) -> dict:
    assert client.post("/entities/US/periods/2026-06", headers=CONTROLLER).status_code == 201
    response = client.post("/journal-entries", headers=AGENT, json=WAGES)
    assert response.status_code == 201, response.text
    return response.json()


def _raw(sql: str, **params) -> None:
    with ENGINE.begin() as connection:
        connection.execute(text(sql), params)


def test_draft_then_approve_then_post(client, draft):
    assert draft["status"] == "draft"
    assert draft["prepared_by"] == "vestly"
    assert draft["reviewed_by"] is None and draft["posted_by"] is None
    early = client.post(f"/journal-entries/{draft['id']}/post", headers=CONTROLLER)
    assert early.status_code == 409
    assert early.json()["error"] == "not_approved"
    approved = client.post(f"/journal-entries/{draft['id']}/approve", headers=CONTROLLER).json()
    assert approved["status"] == "approved"
    assert approved["reviewed_by"] == "dana.ruiz"
    posted = client.post(f"/journal-entries/{draft['id']}/post", headers=CONTROLLER).json()
    assert posted["status"] == "posted"
    assert posted["posted_by"] == "dana.ruiz"
    again = client.post(f"/journal-entries/{draft['id']}/approve", headers=CONTROLLER)
    assert again.status_code == 409


def test_a_rejection_needs_a_reason_and_is_final(client, draft):
    blank = client.post(f"/journal-entries/{draft['id']}/reject", headers=CONTROLLER, json={"note": " "})
    assert blank.status_code == 422
    rejected = client.post(
        f"/journal-entries/{draft['id']}/reject",
        headers=CONTROLLER,
        json={"note": "Wrong department."},
    ).json()
    assert rejected["status"] == "rejected"
    assert rejected["review_note"] == "Wrong department."
    assert client.post(f"/journal-entries/{draft['id']}/approve", headers=CONTROLLER).status_code == 409
    assert client.post(f"/journal-entries/{draft['id']}/post", headers=CONTROLLER).status_code == 409


def test_a_month_locked_after_approval_refuses_the_post(client, draft):
    assert client.post(f"/journal-entries/{draft['id']}/approve", headers=CONTROLLER).status_code == 200
    assert client.post("/entities/US/periods/2026-06/lock", headers=CONTROLLER).status_code == 200
    refused = client.post(f"/journal-entries/{draft['id']}/post", headers=CONTROLLER)
    assert refused.status_code == 409
    assert refused.json()["error"] == "period_locked"
    late = client.post("/journal-entries", headers=AGENT, json=WAGES)
    assert late.status_code == 409


def test_the_database_stops_a_preparer_reviewing_their_own_entry(draft):
    with pytest.raises(IntegrityError, match="preparer_is_not_reviewer"):
        _raw(
            "UPDATE journal_entries SET status = 'approved', reviewed_by = prepared_by, "
            "reviewed_at = prepared_at WHERE id = :id",
            id=draft["id"],
        )


def test_the_database_keeps_posted_entries_final(client, draft):
    client.post(f"/journal-entries/{draft['id']}/approve", headers=CONTROLLER)
    client.post(f"/journal-entries/{draft['id']}/post", headers=CONTROLLER)
    with pytest.raises(IntegrityError, match="journal_is_final"):
        _raw("UPDATE journal_entries SET memo = 'edited later' WHERE id = :id", id=draft["id"])
    with pytest.raises(IntegrityError, match="journal_is_final"):
        _raw("DELETE FROM journal_entries WHERE id = :id", id=draft["id"])
    with pytest.raises(IntegrityError, match="journal_is_final"):
        _raw("UPDATE journal_lines SET debit = 1 WHERE entry_id = :id AND line_number = 1", id=draft["id"])
    with pytest.raises(IntegrityError, match="journal_is_final"):
        _raw("UPDATE journal_entries SET status = 'draft' WHERE id = :id", id=draft["id"])
