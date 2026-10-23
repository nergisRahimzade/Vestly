import json

import pytest
from sqlalchemy import select

from app.auth.identities import IDENTITIES
from app.auth.models import AccessLog, Identity
from app.auth.permissions import JOURNAL_POST, PERIOD_LOCK, PERIOD_OPEN, ROLE_PERMISSIONS, WORLD_LOAD
from app.auth.tokens import token_for
from app.db import SessionLocal
from helpers import AGENT, AUDITOR, CONTROLLER, PROVIDER, STRANGER

WAGES = {
    "entity_code": "US",
    "period": "2026-06",
    "memo": "US Engineering wages for June 2026",
    "lines": [
        {"account_code": "5000", "debit": "100.00", "credit": "0.00", "department": "Engineering"},
        {"account_code": "2100", "debit": "0.00", "credit": "100.00"},
    ],
}
INDIVIDUAL_READS = ("hr", "payroll", "funding", "equity", "bank")


def _access_rows() -> list[AccessLog]:
    with SessionLocal() as session:
        return list(session.scalars(select(AccessLog).order_by(AccessLog.at)))


def test_a_role_header_grants_nothing(client):
    forged = {"X-Role": "controller", "X-Actor": "vestly"}
    response = client.post("/entities/US/periods/2026-06", headers=forged)
    assert response.status_code == 401
    assert response.json()["error"] == "unauthenticated"
    agent_claiming_controller = {**AGENT, "X-Role": "controller"}
    response = client.post("/entities/US/periods/2026-06", headers=agent_claiming_controller)
    assert response.status_code == 403


def test_unknown_token_is_rejected(client):
    response = client.get("/world/people", headers={"Authorization": "Bearer vst_made_up"})
    assert response.status_code == 401


def test_only_the_controller_moves_the_books(client):
    assert client.post("/entities/US/periods/2026-06", headers=AGENT).status_code == 403
    assert client.post("/entities/US/periods/2026-06", headers=CONTROLLER).status_code == 201
    for headers in (AGENT, AUDITOR, PROVIDER, STRANGER):
        assert client.post("/entities/US/periods/2026-06/lock", headers=headers).status_code == 403
        assert client.post("/world/load", headers=headers).status_code == 403
    # Only Vestly prepares; the controller reviews. Nobody does both.
    for headers in (CONTROLLER, AUDITOR, PROVIDER, STRANGER):
        assert client.post("/journal-entries", headers=headers, json=WAGES).status_code == 403
    draft = client.post("/journal-entries", headers=AGENT, json=WAGES)
    assert draft.status_code == 201
    entry_id = draft.json()["id"]
    for headers in (AGENT, AUDITOR, PROVIDER, STRANGER):
        assert client.post(f"/journal-entries/{entry_id}/approve", headers=headers).status_code == 403
        assert client.post(f"/journal-entries/{entry_id}/post", headers=headers).status_code == 403
    assert client.post(f"/journal-entries/{entry_id}/approve", headers=CONTROLLER).status_code == 200
    posted = client.post(f"/journal-entries/{entry_id}/post", headers=CONTROLLER)
    assert posted.status_code == 200
    assert posted.json()["posted_by"] == "dana.ruiz"


def test_only_the_controller_role_can_post_lock_or_load():
    for role, permissions in ROLE_PERMISSIONS.items():
        if role == "controller":
            continue
        assert not permissions & {JOURNAL_POST, PERIOD_LOCK, PERIOD_OPEN, WORLD_LOAD}, role
    assert {role for _, _, role, _ in IDENTITIES} <= set(ROLE_PERMISSIONS)


def test_tokens_are_stored_as_hashes(client):
    with SessionLocal() as session:
        rows = list(session.scalars(select(Identity)))
    assert {row.handle for row in rows} == {handle for handle, _, _, _ in IDENTITIES}
    for row in rows:
        assert token_for(row.handle) not in row.token_hash
    assert len({token_for(handle) for handle, _, _, _ in IDENTITIES}) == len(IDENTITIES)


@pytest.mark.parametrize("resource", INDIVIDUAL_READS)
def test_vestly_cannot_read_one_persons_pay_without_a_case(client, resource):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    listing = client.get(f"/world/{resource}?period=2026-06", headers=AGENT)
    assert listing.status_code == 403
    assert "open case" in listing.json()["message"]
    one = client.get(f"/world/{resource}?period=2026-06&employee_number=NL-US-001", headers=AGENT)
    assert one.status_code == 403
    assert client.get(f"/world/{resource}?period=2026-06", headers=STRANGER).status_code == 403
    assert client.get(f"/world/{resource}?period=2026-06", headers=AUDITOR).status_code == 200


def test_vestly_reads_totals_and_small_groups_stay_hidden(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    totals = client.get("/world/payroll/totals?period=2026-06", headers=AGENT)
    assert totals.status_code == 200
    groups = {(row["entity_code"], row["department"]): row for row in totals.json()}
    engineering = groups[("US", "Engineering")]
    assert engineering["headcount"] >= 3
    assert engineering["suppressed"] is False
    assert engineering["amounts"]["gross_wages"]
    lone = groups[("US", "G&A")]
    assert lone["headcount"] == 1
    assert lone["suppressed"] is True
    assert lone["amounts"] is None
    assert groups[("US", None)]["amounts"]["net_pay"]
    for path in ("hr/headcount", "equity/totals", "funding/totals", "bank/totals"):
        assert client.get(f"/world/{path}?period=2026-06", headers=AGENT).status_code == 200
        assert client.get(f"/world/{path}?period=2026-06", headers=STRANGER).status_code == 403


def test_document_bodies_need_individual_access(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    agent_view = client.get("/world/documents?period=2026-06", headers=AGENT).json()
    assert agent_view
    assert all(row["body"] is None and row["content_hash"] for row in agent_view)
    controller_view = client.get("/world/documents?period=2026-06", headers=CONTROLLER).json()
    assert all(row["body"] for row in controller_view)


def test_every_individual_read_is_logged(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    before = len(_access_rows())
    client.get("/world/payroll/totals?period=2026-06", headers=AGENT)
    assert len(_access_rows()) == before
    client.get("/world/hr?period=2026-06&employee_number=NL-US-005", headers=AUDITOR)
    rows = _access_rows()
    assert len(rows) == before + 1
    assert rows[-1].handle == "riley.auditor"
    assert rows[-1].resource == "hr"
    assert json.loads(rows[-1].subjects) == ["NL-US-005"]
