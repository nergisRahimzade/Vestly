"""Cases and the posting service: work that survives a restart, money that moves once, and a no that changes the plan."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.auth.identities import Actor
from app.cases import service as case_service
from app.cases.close import run_close
from app.cases.models import Case, CaseQuestion
from app.controls import posting
from app.controls.models import BankTransfer
from app.db import ENGINE, build_engine, init_db
from app.record.precedents import search_precedents
from app.world.loader import load_world
from helpers import AGENT, AUDITOR, CONTROLLER, FINANCE_MGR, HR_OPS, as_user

TREASURY = as_user("alex.moreau")
JONAH, RILEY, MAYA = "PAY-2026-06-NL-US-002", "PAY-2026-06-NL-US-005", "PAY-2026-06-NL-US-001"


@pytest.fixture()
def closed(client) -> dict:
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    response = client.post("/close?period=2026-06", headers=AGENT)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def clean_bank():
    posting.BANK_FAULTS.clear()
    yield
    posting.BANK_FAULTS.clear()


def _case(client, key: str) -> dict:
    rows = [row for row in client.get("/cases?period=2026-06", headers=AUDITOR).json() if row["key"] == key]
    assert len(rows) == 1, key
    return client.get(f"/cases/{rows[0]['id']}", headers=AUDITOR).json()


def _funding(client) -> list[dict]:
    return client.get("/funding?period=2026-06", headers=AUDITOR).json()


def _funding_approval(client, funding_id: str) -> dict:
    rows = client.get(f"/approvals?subject_id={funding_id}", headers=AUDITOR).json()
    assert len(rows) == 1
    return rows[0]


def _transfers() -> list[str]:
    with ENGINE.connect() as connection:
        return sorted(row[0] for row in connection.execute(text("SELECT idempotency_key FROM bank_transfers")))


def _question(case: dict, to: str) -> dict:
    return next(row for row in case["questions"] if row["asked_to"] == to and row["status"] == "open")


# ---------------------------------------------------------------- cases


def test_the_close_opens_one_case_per_problem_and_a_second_close_finds_the_same_ones(client, closed):
    keys = sorted(row["key"] for row in closed["cases"])
    assert len(keys) == len(set(keys)) == 15
    maya = _case(client, "bank_detail_change:NL-US-001:2026-06")
    assert (maya["state"], maya["outcome"]) == ("waiting_on_human", "held")
    assert [step["to"] for step in maya["steps"]] == ["open", "investigating", "waiting_on_human"]
    assert all(step["evidence"].startswith("ev_") for step in maya["steps"])
    assert [row["asked_to"] for row in maya["questions"]] == ["lee.park"]
    assert maya["finding"]["authority_row"] == "pay_destination"
    assert {item.get("id") for item in maya["finding"]["basis"]} >= {"BNK-1", "P-2026-03-001"}

    again = client.post("/close?period=2026-06", headers=AGENT).json()
    assert sorted(row["key"] for row in again["cases"]) == keys
    assert again["funding_requests"] == []
    assert len(client.get("/cases?period=2026-06", headers=AUDITOR).json()) == 15


def test_a_case_history_cannot_be_rewritten(client, closed):
    with ENGINE.begin() as connection:
        with pytest.raises(Exception, match="append_only"):
            connection.execute(text("UPDATE case_transitions SET reason = 'nothing happened'"))
    with ENGINE.begin() as connection:
        with pytest.raises(Exception, match="append_only"):
            connection.execute(text("DELETE FROM case_links"))


def test_a_confirmed_call_back_and_hrs_answer_resolve_the_bank_change(client, closed):
    maya = _case(client, "bank_detail_change:NL-US-001:2026-06")
    callback = {"employee_number": "NL-US-001", "account": "9911882277", "outcome": "confirmed", "called_on": "2026-06-28"}
    assert client.post("/controls/callbacks", headers=HR_OPS, json=callback).status_code == 201
    answered = client.post(
        f"/questions/{_question(maya, 'lee.park')['id']}/answer", headers=HR_OPS, json={"answer": "confirmed"}
    ).json()
    assert answered["state"] == "closed"
    assert answered["steps"][-1]["reason"].startswith("Resolved by the answer and the sources")


def test_saying_yes_without_the_evidence_changes_nothing(client, closed):
    maya = _case(client, "bank_detail_change:NL-US-001:2026-06")
    answered = client.post(
        f"/questions/{_question(maya, 'lee.park')['id']}/answer", headers=HR_OPS, json={"answer": "confirmed", "note": "She asked."}
    ).json()
    assert (answered["state"], answered["outcome"]) == ("held", "held")
    assert "the finding still stands" in answered["steps"][-1]["reason"]


def test_only_the_person_asked_may_answer(client, closed):
    maya = _case(client, "bank_detail_change:NL-US-001:2026-06")
    question = _question(maya, "lee.park")
    refused = client.post(f"/questions/{question['id']}/answer", headers=as_user("morgan.li"), json={"answer": "confirmed"})
    assert (refused.status_code, refused.json()["rule"]) == (403, "ACC-1")
    agent = client.post(f"/questions/{question['id']}/answer", headers=AGENT, json={"answer": "confirmed"})
    assert (agent.status_code, agent.json()["rule"]) == (403, "ACC-1")
    assert question["id"] in {row["id"] for row in client.get("/questions/inbox", headers=HR_OPS).json()}


def test_a_partial_answer_gets_a_follow_up_question(client, closed):
    priya = _case(client, "late_termination:NL-US-003:2026-06")
    question = _question(priya, "lee.park")
    missing = client.post(f"/questions/{question['id']}/answer", headers=HR_OPS, json={"answer": "partial"})
    assert missing.status_code == 422
    after = client.post(
        f"/questions/{question['id']}/answer", headers=HR_OPS, json={"answer": "partial", "note": "Exit date confirmed; payroll notice not found yet."}
    ).json()
    assert after["state"] == "waiting_on_human"
    assert [step["to"] for step in after["steps"]][-2:] == ["partially_answered", "waiting_on_human"]
    follow_up = _question(after, "lee.park")
    assert follow_up["id"] != question["id"] and "payroll notice not found yet" in follow_up["question"]


def test_an_unanswered_question_escalates_and_then_needs_a_person(client, closed):
    lukas = _case(client, "missing_grant_approval:NL-DE-001:2026-06")
    assert _question(lukas, "morgan.li")
    assert client.post("/clock/advance?days=4", headers=CONTROLLER).status_code == 200
    lukas = _case(client, "missing_grant_approval:NL-DE-001:2026-06")
    escalated = _question(lukas, "dana.ruiz")
    assert escalated["escalates"] and escalated["question"].startswith("Escalated: morgan.li")
    assert lukas["state"] == "waiting_on_human"
    assert client.post("/clock/advance?days=4", headers=CONTROLLER).status_code == 200
    lukas = _case(client, "missing_grant_approval:NL-DE-001:2026-06")
    assert (lukas["state"], lukas["outcome"]) == ("needs_human", "needs_human")
    assert [step["to"] for step in lukas["steps"]][-3:] == ["waiting_on_human", "expired", "needs_human"]


# ---------------------------------------------------------------- funding


def test_the_half_cleared_batch_re_sends_exactly_the_two_missing_legitimate_lines(client, closed):
    [request] = _funding(client)
    assert [line["payment_id"] for line in request["lines"]] == [JONAH, RILEY]
    assert (request["total"], request["status"], request["requested_by"]) == ("17600.00", "pending", "vestly.payroll")
    approval = _funding_approval(client, request["id"])
    assert approval["inbox_roles"] == ["treasury"] and approval["approvals_needed"] == 1
    assert approval["binding"]["lines"] == [[JONAH, "NL-US-002", "1002003002", "9600.00"], [RILEY, "NL-US-005", "1002003005", "8000.00"]]

    released = client.post(f"/approvals/{approval['id']}/approve", headers=TREASURY)
    assert released.status_code == 200, released.text
    assert _transfers() == [JONAH, RILEY]
    request = _funding(client)[0]
    assert request["status"] == "confirmed"
    assert {line["status"] for line in request["lines"]} == {"confirmed"}


def test_maya_and_cleared_lines_are_refused_and_the_agent_cannot_release(client, closed):
    maya = client.post("/funding", headers=AGENT, json={"period": "2026-06", "payment_ids": [MAYA], "reason": "re-send"})
    assert (maya.status_code, maya.json()["rule"]) == (403, "BNK-1")
    cleared = client.post(
        "/funding", headers=AGENT, json={"period": "2026-06", "payment_ids": ["PAY-2026-06-NL-US-006"], "reason": "re-send"}
    )
    assert (cleared.status_code, cleared.json()["rule"]) == (409, "DUP-1")
    twice = client.post("/funding", headers=AGENT, json={"period": "2026-06", "payment_ids": [JONAH], "reason": "again"})
    assert (twice.status_code, twice.json()["rule"]) == (409, "DUP-1")

    approval = _funding_approval(client, _funding(client)[0]["id"])
    own = client.post(f"/approvals/{approval['id']}/approve", headers=AGENT)
    assert (own.status_code, own.json()["rule"]) == (403, "SOD-1")
    manager = client.post(f"/approvals/{approval['id']}/approve", headers=FINANCE_MGR)
    assert (manager.status_code, manager.json()["rule"]) == (403, "ACC-1")
    controller = client.post(f"/approvals/{approval['id']}/approve", headers=CONTROLLER)
    assert (controller.status_code, controller.json()["rule"]) == (403, "ACC-1")
    assert _transfers() == []


def test_re_releasing_a_confirmed_line_does_nothing(client, closed):
    request = _funding(client)[0]
    client.post(f"/approvals/{_funding_approval(client, request['id'])['id']}/approve", headers=TREASURY)
    again = client.post(f"/funding/{request['id']}/resend", headers=TREASURY).json()
    assert again == {"sent": [], "timed_out": [], "confirmed": []}
    assert _transfers() == [JONAH, RILEY]
    assert {line["sends"] for line in _funding(client)[0]["lines"]} == {1}
    refused = client.post(f"/funding/{request['id']}/resend", headers=AGENT)
    assert (refused.status_code, refused.json()["rule"]) == (403, "ACC-1")


def test_a_timed_out_batch_re_sends_only_the_unconfirmed_line_and_nobody_is_paid_twice(client, closed):
    request = _funding(client)[0]
    posting.BANK_FAULTS.update({JONAH: "lost_reply", RILEY: "drop"})
    client.post(f"/approvals/{_funding_approval(client, request['id'])['id']}/approve", headers=TREASURY)
    # The bank paid Jonah but its reply was lost; Riley's send never arrived.
    assert _transfers() == [JONAH]
    lines = {line["payment_id"]: line for line in _funding(client)[0]["lines"]}
    assert (lines[JONAH]["status"], lines[RILEY]["status"]) == ("confirmed", "sent")

    retry = client.post(f"/funding/{request['id']}/resend", headers=TREASURY).json()
    assert retry["sent"] == [RILEY] and retry["confirmed"] == [RILEY]
    assert _transfers() == [JONAH, RILEY]
    lines = {line["payment_id"]: line for line in _funding(client)[0]["lines"]}
    assert (lines[JONAH]["sends"], lines[RILEY]["sends"]) == (1, 2)


def test_bank_ledger_and_funding_log_agree_to_the_cent_and_the_cases_are_posted(client, closed):
    request = _funding(client)[0]
    client.post(f"/approvals/{_funding_approval(client, request['id'])['id']}/approve", headers=TREASURY)
    recon = client.get(f"/funding/{request['id']}/reconciliation", headers=AUDITOR).json()
    assert [row["agrees"] for row in recon["lines"]] == [False, False]
    cash = [
        entry
        for entry in client.get("/entities/US/journal-entries?period=2026-06", headers=AUDITOR).json()
        if (entry["booking_key"] or "").startswith("funding:cash:")
    ]
    assert sorted(entry["total_debit"] for entry in cash) == ["8000.00", "9600.00"]
    for entry in cash:
        assert entry["prepared_by"] == "vestly.payroll"
        assert client.post(f"/journal-entries/{entry['id']}/approve", headers=FINANCE_MGR).json()["status"] == "approved"
        first = client.post(f"/journal-entries/{entry['id']}/post", headers=CONTROLLER)
        second = client.post(f"/journal-entries/{entry['id']}/post", headers=CONTROLLER)
        assert first.json()["status"] == second.json()["status"] == "posted"
    posted_events = client.get("/audit/events?action=journal_posted&limit=1000", headers=AUDITOR).json()
    assert len([event for event in posted_events if event["payload"]["booking_key"].startswith("funding:cash:")]) == 2

    recon = client.get(f"/funding/{request['id']}/reconciliation", headers=AUDITOR).json()
    assert [(row["requested"], row["bank"], row["ledger"], row["agrees"]) for row in recon["lines"]] == [
        ("9600.00", "9600.00", "9600.00", True),
        ("8000.00", "8000.00", "8000.00", True),
    ]
    for key in (f"uncleared_funding:{JONAH}:2026-06", f"uncleared_funding:{RILEY}:2026-06"):
        assert _case(client, key)["state"] == "posted"


# ---------------------------------------------------------------- a no changes the plan


def test_a_declined_funding_request_holds_the_cases_and_is_not_asked_again(client, closed):
    request = _funding(client)[0]
    approval = _funding_approval(client, request["id"])
    no_reason = client.post(f"/approvals/{approval['id']}/decline", headers=TREASURY, json={})
    assert no_reason.status_code == 422
    declined = client.post(
        f"/approvals/{approval['id']}/decline", headers=TREASURY, json={"note": "Both were paid by cheque on 30 June."}
    )
    assert declined.status_code == 200
    assert _transfers() == []
    jonah = _case(client, f"uncleared_funding:{JONAH}:2026-06")
    assert (jonah["state"], jonah["outcome"]) == ("held", "held")
    assert "paid by cheque" in jonah["steps"][-2]["reason"]
    assert _funding(client)[0]["status"] == "declined"

    again = client.post("/close?period=2026-06", headers=AGENT).json()
    assert again["funding_requests"] == []
    assert len(_funding(client)) == 1


def test_a_controller_decline_can_become_a_precedent(client, closed):
    priya = _case(client, "late_termination:NL-US-003:2026-06")
    [entry_link] = [row for row in priya["links"] if row["resource"] == "journal_entry"]
    # Her forfeiture is the equity seat's case; it is not tied to the payroll seat's receivable.
    forfeiture = _case(client, "forfeiture_still_expensed:NL-US-003:2026-06")
    assert entry_link["id"] not in {row["id"] for row in forfeiture["links"]}
    request = client.get(f"/approvals?subject_id={entry_link['id']}&status=pending", headers=AUDITOR).json()[0]
    body = {
        "note": "Recover leaver overpayments through the final settlement, not a receivable.",
        "precedent": {"rule": "DUP-1", "scope": {"entity": "US"}},
    }
    forbidden = client.post(f"/approvals/{request['id']}/decline", headers=FINANCE_MGR, json=body)
    assert (forbidden.status_code, forbidden.json()["rule"]) == (403, "ACC-1")
    assert client.post(f"/approvals/{request['id']}/decline", headers=CONTROLLER, json=body).status_code == 200

    priya = _case(client, "late_termination:NL-US-003:2026-06")
    assert (priya["state"], priya["outcome"]) == ("held", "held")
    from app.db import SessionLocal

    with SessionLocal() as session:
        [ruling] = search_precedents(session, "2026-07-02", rule="DUP-1", entity="US")
        assert ruling.decided_by == "dana.ruiz" and "final settlement" in ruling.ruling


# ---------------------------------------------------------------- restarts


def test_a_restart_mid_wait_resumes_every_case(tmp_path, monkeypatch):
    """Answer stored, then the process dies before acting on it; a payment times out. A new process finishes both."""
    url = f"sqlite+pysqlite:///{(tmp_path / 'restart.db').as_posix()}"
    engine = build_engine(url)
    init_db(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    load_world(session, Actor("dana.ruiz", "controller"))
    run_close(session, "2026-06")
    maya = session.scalar(select(Case).where(Case.key == "bank_detail_change:NL-US-001:2026-06"))
    question = session.scalar(select(CaseQuestion).where(CaseQuestion.case_id == maya.id))

    def crash(*_args, **_kwargs):
        raise RuntimeError("power cut")

    monkeypatch.setattr(case_service, "apply_answers", crash)
    with pytest.raises(RuntimeError):
        case_service.answer(session, question.id, Actor("lee.park", "hr_ops"), "declined", "She never sent that email.")
    from app.controls import approvals

    request = session.scalar(select(approvals.ApprovalRequest).where(approvals.ApprovalRequest.kind == "funding_request"))
    posting.BANK_FAULTS[RILEY] = "drop"
    approvals.decide(session, request.id, Actor("alex.moreau", "treasury"), approve=True, note=None)
    session.close()
    engine.dispose()
    monkeypatch.undo()

    engine = build_engine(url)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    assert session.get(Case, maya.id).state == "waiting_on_human"
    result = case_service.resume(session)
    assert result == {"answers_applied": 1, "funding_resent": [RILEY]}
    maya = session.get(Case, maya.id)
    assert (maya.state, maya.outcome) == ("held", "held")
    assert sorted(row.idempotency_key for row in session.scalars(select(BankTransfer))) == [JONAH, RILEY]
    assert case_service.resume(session) == {"answers_applied": 0, "funding_resent": []}
    session.close()
    engine.dispose()


def test_every_action_names_the_record_it_touches_and_none_deletes(client):
    policy = client.get("/policy").json()
    granted = {action for actions in policy["roles"].values() for action in actions}
    assert granted <= set(policy["actions"])
    assert {row["verb"] for row in policy["actions"].values()} <= {"create", "read", "update"}
    assert policy["actions"]["request_funding"] == {"resource": "funding_request", "verb": "create"}
    refused = client.post("/controls/check", headers=AGENT, json={"action": "release_funding"}).json()
    assert refused["rule"] == "ACC-1" and "update on funding request" in refused["reason"]
