"""Task 19, the API the UI stands on: the login picker, the account block from HR's call-back, and the read views."""

import json

import pytest
from sqlalchemy import select

from app.auth.identities import Actor
from app.controls.callbacks import check_pay_destination
from app.controls.models import BankTransfer
from app.db import SessionLocal
from app.record.models import EvidenceEvent, Precedent
from helpers import AUDITOR, CONTROLLER, FINANCE_MGR, PAYROLL_AGENT, as_user

MAYA = "bank_detail_change:NL-US-001:2026-06"
FRAUD_ACCOUNT = "9911882277"


@pytest.fixture()
def june(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    assert client.post("/close?period=2026-06", headers=PAYROLL_AGENT).status_code == 200
    return client


def _case(client, key: str) -> dict:
    return next(row for row in client.get("/cases?period=2026-06", headers=AUDITOR).json() if row["key"] == key)


# ---------------------------------------------------------------- the login picker


def test_the_picker_lists_people_and_never_vestly_and_its_token_carries_the_server_role(client):
    people = client.get("/auth/identities").json()
    handles = {row["handle"] for row in people}
    assert "dana.ruiz" in handles and "lee.park" in handles
    assert not handles & {"vestly.payroll", "vestly.equity"}
    signed = client.post("/auth/login", json={"handle": "lee.park", "role": "controller"}).json()
    assert signed["role"] == "hr_ops"
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {signed['token']}"}).json()
    assert (me["handle"], me["role"]) == ("lee.park", "hr_ops")
    refused = client.post("/close?period=2026-06", headers={"Authorization": f"Bearer {signed['token']}"})
    assert refused.status_code == 403 and refused.json()["rule"] == "ACC-1"


def test_nobody_signs_in_as_a_vestly_seat_and_every_sign_in_is_logged(client):
    refused = client.post("/auth/login", json={"handle": "vestly.payroll"})
    assert (refused.status_code, refused.json()["rule"]) == (403, "ACC-1")
    assert client.post("/auth/login", json={"handle": "nobody.here"}).status_code == 404
    client.post("/auth/login", json={"handle": "riley.auditor"})
    events = client.get("/audit/events?action=signed_in", headers=AUDITOR).json()
    assert [event["actor"] for event in events] == ["riley.auditor"]


def test_the_picker_can_be_turned_off(client, monkeypatch):
    monkeypatch.setenv("VESTLY_DEMO_LOGIN", "0")
    assert client.get("/auth/identities").status_code == 404
    assert client.post("/auth/login", json={"handle": "dana.ruiz"}).status_code == 404


# ---------------------------------------------------------------- HR's call-back becomes a block on the account


def _hr_says_fraud_and_vestly_proposes(client) -> dict:
    """HR's call-back disproves Maya's email (the playbook), then Vestly works the case again and proposes the block."""
    client.post("/simulate/approvers?period=2026-06", headers=CONTROLLER)
    case = _case(client, MAYA)
    assert (case["state"], case["outcome"]) == ("held", "held")
    run = client.post(f"/agent/cases/{case['id']}/run", headers=CONTROLLER).json()
    assert run["status"] == "reported", run
    return run


def test_after_hrs_call_back_disproves_the_email_vestly_proposes_blocking_the_account_and_says_it_waits(june):
    run = _hr_says_fraud_and_vestly_proposes(june)
    assert f"Proposed a BNK-1 precedent blocking account {FRAUD_ACCOUNT} in every entity; waiting for dana.ruiz to confirm it." in run["shown"]
    assert {"tool": "propose_precedent", "status": "pending"} in [{k: item[k] for k in ("tool", "status")} for item in run["tools"]]
    with SessionLocal() as db:
        assert db.scalar(select(Precedent).where(Precedent.scope_json.like(f"%{FRAUD_ACCOUNT}%"))) is None, "nothing is a precedent until the controller confirms"
    again = june.post(f"/agent/cases/{_case(june, MAYA)['id']}/run", headers=CONTROLLER).json()
    assert "propose_precedent" not in [item["tool"] for item in again["tools"]], "the proposal is not repeated"


def test_once_the_controller_confirms_the_account_takes_no_pay_from_anyone_in_any_entity(june):
    _hr_says_fraud_and_vestly_proposes(june)
    [question] = [row for row in june.get("/questions/inbox", headers=CONTROLLER).json() if row["question"].startswith("Proposed precedent under BNK-1")]
    june.post(f"/questions/{question['id']}/answer", headers=CONTROLLER, json={"answer": "confirmed"})
    with SessionLocal() as db:
        ruling = db.scalar(select(Precedent).where(Precedent.scope_json.like(f"%{FRAUD_ACCOUNT}%")))
        assert json.loads(ruling.scope_json) == {"account": FRAUD_ACCOUNT} and ruling.decided_by == "dana.ruiz"
        hr = Actor("lee.park", "hr_ops")
        from app.controls.callbacks import record_callback

        # Even a confirmed call-back cannot reopen a blocked account, for a UK employee as for Maya.
        record_callback(db, hr, "NL-UK-001", FRAUD_ACCOUNT, "confirmed")
        verdict = check_pay_destination(db, "NL-UK-001", FRAUD_ACCOUNT, "2026-06", "2026-06-30")
        assert not verdict.allowed and verdict.rule == "BNK-1" and ruling.id in verdict.reason
        # Before the ruling, the same payment would have been judged by the rulings then in force.
        earlier = check_pay_destination(db, "NL-UK-001", FRAUD_ACCOUNT, "2026-06", "2026-06-30", checked_on="2026-06-30")
        assert ruling.id not in (earlier.reason or "")
    found = june.get(f"/precedents/search?on=2026-07-02&rule=BNK-1&account={FRAUD_ACCOUNT}", headers=AUDITOR).json()
    assert [row["id"] for row in found] == [ruling.id]
    assert ruling.id in [row["id"] for row in june.get("/precedents", headers=AUDITOR).json()]


def test_vestly_cannot_scope_a_precedent_to_an_account_the_case_does_not_name(june):
    from app.agent.execute import AgentContext, execute
    from app.cases.models import Case

    with SessionLocal() as db:
        case = db.scalar(select(Case).where(Case.key == MAYA))
        ctx = AgentContext(db, case, Actor("vestly.payroll", "agent"), "run-scope")
        result = execute(ctx, "propose_precedent", {"rule": "BNK-1", "scope_entity": None, "scope_employee": None, "scope_account": "1234567890", "ruling": "Block it."})
        assert result.status == "refused" and "not named in this case's finding" in result.error


def test_a_block_ruled_after_the_request_stops_the_release_before_the_bank_hears_of_it(june):
    [request] = june.get("/approvals/inbox", headers=as_user("alex.moreau")).json()
    account = request["binding"]["lines"][0][2]
    june.post("/precedents", headers=CONTROLLER, json={"rule": "BNK-1", "scope": {"account": account}, "ruling": "Blocked pending review.", "reason": "Test block.", "valid_from": "2026-07-02"})
    refused = june.post(f"/approvals/{request['id']}/approve", headers=as_user("alex.moreau"), json={"note": "release"})
    assert refused.status_code == 403 and refused.json()["rule"] == "BNK-1"
    with SessionLocal() as db:
        assert db.scalars(select(BankTransfer)).all() == []


# ---------------------------------------------------------------- read views


def test_an_employees_history_is_for_individual_readers_only_and_every_read_is_logged(june):
    history = june.get("/memory/employees/NL-US-001", headers=AUDITOR)
    assert history.status_code == 200 and history.json()["employee_number"] == "NL-US-001"
    refused = june.get("/memory/employees/NL-US-001", headers=PAYROLL_AGENT)
    assert (refused.status_code, refused.json()["rule"]) == (403, "PRV-1")
    assert june.get("/memory/employees/NL-US-001", headers=as_user("head.engineering")).status_code == 403
    reads = june.get("/audit/events?action=individual_read&limit=1000", headers=AUDITOR).json()
    assert any(event["actor"] == "riley.auditor" and event["payload"]["resource"] == "employee_history" for event in reads)
    baseline = june.get("/memory/departments/US/Engineering", headers=FINANCE_MGR)
    assert baseline.status_code == 200 and "months" in baseline.json()


def test_a_cited_document_opens_by_its_hash_with_the_body_only_for_individual_readers(june):
    maya = june.get(f"/cases/{_case(june, MAYA)['id']}", headers=AUDITOR).json()
    sha = next(item["doc_sha256"] for item in maya["finding"]["basis"] if item["type"] == "document")
    full = june.get(f"/world/documents/{sha}", headers=AUDITOR).json()
    assert "9911882277" in full["body"] and full["content_hash"] == sha
    assert june.get(f"/world/documents/{sha}", headers=FINANCE_MGR).json()["body"] is None
    assert june.get("/world/documents/" + "0" * 64, headers=AUDITOR).status_code == 404


def test_the_scorer_report_is_shown_as_a_real_run_wrote_it_or_says_how_to_make_one(client, tmp_path, monkeypatch):
    from app.eval import routes

    monkeypatch.setattr(routes, "REPORTS", tmp_path)
    missing = client.get("/eval/report?world=cast", headers=AUDITOR)
    assert missing.status_code == 404 and "python -m app.eval.score --world cast" in missing.json()["message"]
    (tmp_path / "score-cast.json").write_text(json.dumps({"generated_at": "2026-10-23T00:00:00+00:00", "command": "x", "world": "cast", "exit_code": 0, "report": {"passed": True}, "runs": [1]}))
    shown = client.get("/eval/report?world=cast", headers=AUDITOR).json()
    assert shown["report"] == {"passed": True} and "runs" not in shown
    assert client.get("/eval/report?world=cast", headers=as_user("lee.park")).status_code == 403
