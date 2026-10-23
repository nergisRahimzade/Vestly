"""Every write leaves a chained evidence row, a journal line walks back to its sources,
and a March ruling is found again in June only where its scope and dates say so."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db import ENGINE, SessionLocal
from app.world.models import SourceDocument
from helpers import AGENT, AUDITOR, CONTROLLER, EQUITY_AGENT, FINANCE_MGR, STRANGER


def _events(client, **filters) -> list[dict]:
    query = "&".join(f"{key}={value}" for key, value in filters.items())
    response = client.get(f"/audit/events?limit=1000&{query}", headers=AUDITOR)
    assert response.status_code == 200, response.text
    return response.json()


def _june_payroll(client) -> dict:
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    response = client.post("/payroll/drafts?period=2026-06", headers=AGENT)
    assert response.status_code == 200, response.text
    return response.json()


def test_every_write_path_leaves_evidence_and_the_chain_holds(client):
    body = _june_payroll(client)
    wages = next(entry for entry in body["journals"] if entry["booking_key"] == "payroll:wages:US:2026-06")
    # US wages are above the approval limit: the finance manager and the controller both approve.
    assert client.post(f"/journal-entries/{wages['id']}/approve", headers=FINANCE_MGR).json()["status"] == "draft"
    assert client.post(f"/journal-entries/{wages['id']}/approve", headers=CONTROLLER).json()["status"] == "approved"
    assert client.post(f"/journal-entries/{wages['id']}/post", headers=CONTROLLER).status_code == 200
    client.post("/payroll/drafts?period=2026-06", headers=AGENT)
    client.post("/equity/drafts?period=2026-06", headers=EQUITY_AGENT)
    client.post("/reconciliations?period=2026-06", headers=AGENT)
    client.get("/world/hr?period=2026-06&employee_number=NL-US-005", headers=AUDITOR)
    client.post("/entities/US/periods/2026-03/reopen", headers=CONTROLLER, json={"reason": "Test reopen"})
    client.post("/entities/US/periods/2026-03/lock", headers=CONTROLLER)

    actions = {event["action"] for event in _events(client)}
    assert {
        "documents_received",
        "drop_received",
        "period_opened",
        "period_locked",
        "journal_imported",
        "precedent_ruled",
        "journal_drafted",
        "journal_approved",
        "journal_posted",
        "journal_withdrawn",
        "schedule_run",
        "reconciliation_run",
        "individual_read",
        "period_reopened",
        "period_relocked",
    } <= actions

    check = client.get("/audit/verify", headers=AUDITOR).json()
    assert check["ok"] is True
    assert check["first_broken"] is None
    assert check["events"] == len(_events(client))

    drafted = next(
        event
        for event in _events(client, subject_type="journal_entry", subject_id=wages["id"])
        if event["action"] == "journal_drafted"
    )
    assert drafted["actor"] == "vestly.payroll"
    assert drafted["payload"]["booking_key"] == "payroll:wages:US:2026-06"
    assert drafted["policy_hash"] == client.get("/policy").json()["content_hash"]
    assert drafted["basis"] and all(item["type"] == "document" for item in drafted["basis"])


def test_the_database_refuses_to_change_or_delete_the_record(client):
    _june_payroll(client)
    for statement in (
        "UPDATE evidence_events SET actor = 'someone else'",
        "DELETE FROM evidence_events",
        "UPDATE precedents SET ruling = 'changed'",
        "DELETE FROM precedents",
        "UPDATE source_documents SET body = 'changed'",
        "DELETE FROM source_documents",
    ):
        with pytest.raises(IntegrityError, match="append_only"):
            with ENGINE.begin() as connection:
                connection.execute(text(statement))


def test_tampering_with_one_row_breaks_verification_at_that_row(client):
    _june_payroll(client)
    target = _events(client, action="journal_drafted")[0]
    with ENGINE.begin() as connection:
        # Someone with direct database access removes the guard and edits history.
        connection.execute(text("DROP TRIGGER evidence_events_append_only_update"))
        connection.execute(
            text("UPDATE evidence_events SET payload_json = '{\"total\":\"1.00\"}' WHERE seq = :seq"),
            {"seq": target["seq"]},
        )
    check = client.get("/audit/verify", headers=AUDITOR).json()
    assert check["ok"] is False
    assert check["first_broken"] == target["seq"]
    assert "changed" in check["reason"]


def test_a_june_wage_line_traces_to_rileys_register_row(client):
    body = _june_payroll(client)
    wages = next(entry for entry in body["journals"] if entry["booking_key"] == "payroll:wages:US:2026-06")
    engineering = next(
        line for line in wages["lines"] if line["account_code"] == "5000" and line["department"] == "Engineering"
    )
    assert client.get(f"/audit/trace/journal-line/{engineering['id']}", headers=AGENT).status_code == 403
    assert client.get(f"/audit/trace/journal-line/{engineering['id']}", headers=STRANGER).status_code == 403

    trace = client.get(f"/audit/trace/journal-line/{engineering['id']}", headers=AUDITOR).json()
    assert trace["line"]["debit"] == engineering["debit"]
    assert trace["people"]["prepared_by"] == "vestly.payroll"
    assert trace["policy"]["hash"] == client.get("/policy").json()["content_hash"]
    riley = next(source for source in trace["sources"] if source.get("employee_number") == "NL-US-005")
    assert riley["table"] == "payroll_register_rows"
    assert riley["gross_wages"] == "10000.00"
    with SessionLocal() as session:
        register = session.scalar(
            select(SourceDocument).where(SourceDocument.system == "payroll", SourceDocument.period == "2026-06")
        )
    assert riley["document"] == register.content_hash
    assert register.content_hash in {document["sha256"] for document in trace["documents"]}
    assert {source["employee_number"] for source in trace["sources"]} == {"NL-US-001", "NL-US-002", "NL-US-005"}
    assert [event["action"] for event in trace["events"]] == ["journal_drafted"]


def test_an_overpayment_line_traces_to_hr_and_the_march_register(client):
    body = _june_payroll(client)
    overpayment = next(entry for entry in body["journals"] if entry["booking_key"].startswith("payroll:overpayment:"))
    receivable = next(line for line in overpayment["lines"] if line["account_code"] == "1200")
    trace = client.get(f"/audit/trace/journal-line/{receivable['id']}", headers=CONTROLLER).json()
    tables = {source["table"]: source for source in trace["sources"]}
    assert tables["payroll_register_rows"]["period"] == "2026-03"
    assert tables["hr_census_rows"]["termination_date"] == "2026-03-12"


def test_an_equity_true_up_line_traces_to_the_subledger_and_the_lines_it_offsets(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    body = client.post("/equity/drafts?period=2026-06", headers=EQUITY_AGENT).json()
    us = next(entry for entry in body["journals"] if entry["booking_key"] == "equity:trueup:US:2026-06")
    product = next(line for line in us["lines"] if line["account_code"] == "5200")
    trace = client.get(f"/audit/trace/journal-line/{product['id']}", headers=AUDITOR).json()
    grants = {source.get("grant") for source in trace["sources"] if source["table"] == "equity_grant_months"}
    assert {"GR-US-003", "GR-US-006"} <= grants
    offsets = [source for source in trace["sources"] if source["table"] == "journal_lines"]
    assert offsets and all(source["found"] for source in offsets)
    assert trace["provenance"]["schedule_run"] == body["schedule_run"]


def test_the_march_ruling_is_found_in_june_for_maya_and_nowhere_else(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    ruling = client.get("/precedents/P-2026-03-001", headers=AGENT).json()
    assert ruling["rule"] == "BNK-1"
    assert ruling["decided_by"] == "dana.ruiz"
    assert ruling["scope"] == {"entity": "US"}
    with SessionLocal() as session:
        email = session.scalar(
            select(SourceDocument).where(SourceDocument.title == "Controller rejection of Jonah Hale bank change")
        )
    assert ruling["source_document_hash"] == email.content_hash

    def search(**params) -> list[str]:
        query = "&".join(f"{key}={value}" for key, value in params.items())
        response = client.get(f"/precedents/search?{query}", headers=AGENT)
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()]

    assert search(on="2026-06-28", rule="BNK-1", entity="US", employee="NL-US-001") == ["P-2026-03-001"]
    assert search(on="2026-03-05", rule="BNK-1", entity="US") == []
    assert search(on="2026-06-28", rule="BNK-1", entity="UK") == []
    assert search(on="2026-06-28", rule="BNK-1") == []
    assert search(on="2026-06-28", rule="GRT-1", entity="US") == []


def test_a_precedent_is_overruled_by_a_new_one_never_edited(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    proposal = {
        "rule": "BNK-1",
        "scope": {},
        "ruling": "Block bank changes without a call-back in every entity.",
        "reason": "Maya Chen's emailed request was fraudulent.",
        "valid_from": "2026-07-03",
        "overrules": "P-2026-03-001",
    }
    assert client.post("/precedents", headers=AGENT, json=proposal).status_code == 403
    created = client.post("/precedents", headers=CONTROLLER, json=proposal)
    assert created.status_code == 201, created.text
    new_id = created.json()["id"]
    assert new_id == "P-2026-07-001"
    assert client.post("/precedents", headers=CONTROLLER, json=proposal).status_code == 409

    june = client.get("/precedents/search?on=2026-06-28&rule=BNK-1&entity=US", headers=AGENT).json()
    july = client.get("/precedents/search?on=2026-07-10&rule=BNK-1&entity=UK", headers=AGENT).json()
    assert [row["id"] for row in june] == ["P-2026-03-001"]
    assert [row["id"] for row in july] == [new_id]
    assert client.get("/precedents/P-2026-03-001", headers=AUDITOR).json()["ruling"].startswith("Reject")

    bad_scope = {**proposal, "scope": {"department": "Engineering"}, "overrules": None}
    assert client.post("/precedents", headers=CONTROLLER, json=bad_scope).status_code == 422
    ruled = [event for event in _events(client, action="precedent_ruled") if event["subject_id"] == new_id]
    assert ruled[0]["actor"] == "dana.ruiz"
