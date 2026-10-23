"""Task 18: replay what was known on a date, defend a decision from its record, and let simulated people answer."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agent.execute import AgentContext, execute
from app.approvers.simulate import ClaudeVoice, Reply, simulate
from app.auth.identities import Actor
from app.cases.close import run_close
from app.cases.models import Case, CaseQuestion
from app.db import SessionLocal
from app.record.challenge import ChallengeAnswer, ClaudeChallenger, challenge
from app.record.models import EvidenceEvent, Precedent, PrecedentProposal
from app.record.replay import replay
from app.world.loader import load_world
from helpers import AUDITOR, CONTROLLER, PAYROLL_AGENT, as_user

CONTROLLER_ACTOR = Actor("dana.ruiz", "controller")
MAYA = "bank_detail_change:NL-US-001:2026-06"


@pytest.fixture()
def june(client):
    with SessionLocal() as db:
        load_world(db, CONTROLLER_ACTOR)
        run_close(db, "2026-06")
        yield db


def _case(db, key: str) -> Case:
    return db.scalar(select(Case).where(Case.key == key))


class Liar:
    def __init__(self, answer: ChallengeAnswer):
        self.reply = answer

    def answer(self, question, context):
        return self.reply


# ---------------------------------------------------------------- challenge


def test_the_controllers_push_back_on_maya_is_answered_from_the_record_and_the_hold_stands(june):
    case = _case(june, MAYA)
    result = challenge(june, case.id, "She has been here four years, just run it.", CONTROLLER_ACTOR)
    assert result["blocked"] is False and result["decision_stands"] is True
    assert "P-2026-03-001" in result["shown"] and "BNK-1" in result["shown"]
    assert case.reason_code == "bank_change_unverified"
    from app.reconcile.models import Reconciliation

    assert june.get(Reconciliation, case.finding_id).policy_hash in result["shown"]
    assert result["decision"] == {"state": "waiting_on_human", "outcome": "held"}
    actions = [row.action for row in june.scalars(select(EvidenceEvent).where(EvidenceEvent.case_id == case.id))]
    assert actions[-2:] == ["challenge_asked", "challenge_answered"]
    assert all(item.startswith("ev_") for item in result["citations"])


@pytest.mark.parametrize(
    "text, citations, problem",
    [
        ("The hold stands.", ["ev_999999"], "not in this decision's record"),
        ("The hold stands.", [], "Cites no evidence row"),
        ("Maya was paid 15,250.00 last month.", ["<first>"], "The figure 15,250.00 is not in the record"),
        ('HR wrote "she confirmed the new account".', ["<first>"], 'The quote "she confirmed the new account" is not in the record'),
    ],
    ids=["invented citation", "no citation", "invented figure", "invented quote"],
)
def test_an_unsupported_answer_to_a_challenge_is_blocked_and_the_record_is_shown(june, text, citations, problem):
    case = _case(june, MAYA)
    first = june.scalar(select(EvidenceEvent.id).where(EvidenceEvent.case_id == case.id).limit(1))
    answer = ChallengeAnswer(answer=text, decision_stands=True, citations=[first if item == "<first>" else item for item in citations])
    result = challenge(june, case.id, "Why hold?", CONTROLLER_ACTOR, challenger=Liar(answer))
    assert result["blocked"] is True
    assert any(problem in item for item in result["problems"]), result["problems"]
    assert result["shown"].startswith(f"The record for {MAYA}")
    assert result["decision"]["outcome"] == "held"
    assert june.scalar(select(EvidenceEvent).where(EvidenceEvent.action == "false_claim", EvidenceEvent.case_id == case.id)) is not None


def test_a_challenge_that_finds_a_mistake_asks_the_controller_and_changes_nothing_by_itself(june):
    case = _case(june, "late_termination:NL-US-003:2026-06")
    first = june.scalar(select(EvidenceEvent.id).where(EvidenceEvent.case_id == case.id).limit(1))
    reviewer = Liar(ChallengeAnswer(answer="The reviewer is right to question the receivable.", decision_stands=False, citations=[first]))
    before = (case.state, case.outcome)
    result = challenge(june, case.id, "Should the receivable wait?", Actor("riley.auditor", "auditor"), challenger=reviewer)
    assert result["decision_stands"] is False and result["blocked"] is False
    june.refresh(case)
    assert (case.state, case.outcome) == before
    asked = june.scalars(select(CaseQuestion).where(CaseQuestion.case_id == case.id, CaseQuestion.asked_to == "dana.ruiz")).all()
    assert any("A reviewer (riley.auditor) challenged" in row.question for row in asked)


def test_only_reviewers_may_challenge(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    client.post("/close?period=2026-06", headers=PAYROLL_AGENT)
    case = next(row for row in client.get("/cases?period=2026-06", headers=AUDITOR).json() if row["key"] == MAYA)
    for headers in (PAYROLL_AGENT, as_user("stranger"), as_user("lee.park")):
        refused = client.post(f"/cases/{case['id']}/challenge", headers=headers, json={"question": "Why?"})
        assert (refused.status_code, refused.json()["rule"]) == (403, "ACC-1")
    allowed = client.post(f"/cases/{case['id']}/challenge", headers=AUDITOR, json={"question": "Why?"})
    assert allowed.status_code == 200 and allowed.json()["decision_stands"] is True


# ---------------------------------------------------------------- replay


def test_replay_before_2_april_shows_priya_active_and_after_her_record_terminated(june):
    before = replay(june, "2026-04-01", employee="NL-US-003")
    assert before["drops_known"] == ["2026-01", "2026-02"]
    assert before["employee"]["status"] == "active" and before["employee"]["termination_date"] is None
    after = replay(june, "2026-05-02", employee="NL-US-003")
    assert after["employee"]["status"] == "terminated" and after["employee"]["termination_recorded_on"] == "2026-04-02"


def test_replay_shows_the_rulings_in_force_and_the_case_as_it_stood(june):
    assert [row["id"] for row in replay(june, "2026-03-05", employee="NL-US-001")["precedents_in_force"]] == []
    assert [row["id"] for row in replay(june, "2026-03-10", employee="NL-US-001")["precedents_in_force"]] == ["P-2026-03-001"]
    case = _case(june, MAYA)
    state = replay(june, "2026-07-02", employee="NL-US-001", case_id=case.id)
    assert [step["to"] for step in state["case"]["steps"]] == ["open", "investigating", "waiting_on_human"]
    assert state["policy_hash"] and state["latest_month_known"] == "2026-06"
    with pytest.raises(Exception):
        replay(june, "July", employee="NL-US-001")


# ---------------------------------------------------------------- asking for one person's pay


def test_a_salary_request_is_refused_with_totals_offered_and_answered_for_the_controller(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    refused = client.post("/agent/ask", headers=as_user("head.engineering"), json={"question": "What is Maya Chen's salary?"}).json()
    assert refused["status"] == "refused" and refused["rule"] == "PRV-1"
    offered = refused["offered"]
    assert offered["entity_code"] == "US" and offered["department"] == "Engineering" and offered["headcount"] >= 3
    assert "Maya" not in json.dumps(offered)
    answered = client.post("/agent/ask", headers=CONTROLLER, json={"question": "What is Maya Chen's salary?"}).json()
    assert answered["status"] == "ok" and "15000.00 USD a month" in answered["answer"]
    events = client.get("/audit/events?action=refused&limit=1000", headers=AUDITOR).json()
    assert any(event["reason_code"] == "PRV-1" and event["actor"] == "head.engineering" for event in events)


# ---------------------------------------------------------------- precedents Vestly proposes


def test_a_proposed_precedent_exists_only_once_the_controller_confirms_it(june):
    case = _case(june, MAYA)
    ctx = AgentContext(june, case, Actor("vestly.payroll", "agent"), "run-propose")
    proposal = execute(ctx, "propose_precedent", {"rule": "BNK-1", "scope_entity": None, "scope_employee": None, "ruling": "Account 9911882277 is fraudulent: never pay it, in any entity."})
    assert proposal.status == "pending"
    row = june.scalar(select(PrecedentProposal).where(PrecedentProposal.case_id == case.id))
    assert row.status == "proposed" and json.loads(row.scope_json) == {}
    assert june.scalar(select(Precedent).where(Precedent.ruling.like("%9911882277%"))) is None
    from app.cases.service import answer

    answer(june, row.question_id, CONTROLLER_ACTOR, "confirmed")
    june.refresh(row)
    assert row.status == "confirmed"
    made = june.get(Precedent, row.precedent_id)
    assert made.rule == "BNK-1" and made.decided_by == "dana.ruiz" and json.loads(made.scope_json) == {}
    bad = execute(ctx, "propose_precedent", {"rule": "XYZ-9", "scope_entity": None, "scope_employee": None, "ruling": "nothing"})
    assert bad.status == "refused"


# ---------------------------------------------------------------- simulated people


def test_the_simulated_people_answer_through_the_same_doors_and_the_story_plays_out(june):
    log = simulate(june, "2026-06")
    said = {item["case"]: item for item in log if "case" in item}
    maya = said[MAYA]
    assert maya["person"] == "lee.park" and maya["read_as"] == "declined" and maya["counted"] is True
    case = _case(june, MAYA)
    assert (case.state, case.outcome) == ("held", "held")
    from app.controls.models import BankTransfer, Callback

    callback = june.scalar(select(Callback).where(Callback.employee_number == "NL-US-001"))
    assert callback.outcome == "denied" and callback.called_by == "lee.park"
    priya = said["late_termination:NL-US-003:2026-06"]
    assert priya["read_as"] == "confirmed" and priya["counted"] is True
    released = [item for item in log if item.get("approved")]
    assert released and released[0]["person"] == "alex.moreau"
    assert sorted(row.payment_id for row in june.scalars(select(BankTransfer))) == ["PAY-2026-06-NL-US-002", "PAY-2026-06-NL-US-005"]


def test_a_reading_that_misses_what_the_person_meant_is_rejected_and_counts_for_nothing(june):
    replies = {("bank_detail_change", "lee.park"): Reply("Let me check with the manager first.", "declined")}
    log = simulate(june, "2026-06", replies=replies, approvals_policy={})
    [entry] = [item for item in log if item.get("case") == MAYA]
    assert entry["counted"] is False
    case = _case(june, MAYA)
    open_questions = june.scalars(select(CaseQuestion).where(CaseQuestion.case_id == case.id, CaseQuestion.status == "open")).all()
    assert open_questions, "a rejected reading leaves the question open, answerable by hand"


def test_the_claude_challenger_and_voice_send_the_planned_requests():
    sent = []

    class Messages:
        def parse(self, **kwargs):
            sent.append(("parse", kwargs))
            return SimpleNamespace(stop_reason="end_turn", parsed_output=ChallengeAnswer(answer="x", decision_stands=True, citations=["ev_000001"]))

        def create(self, **kwargs):
            sent.append(("create", kwargs))
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="No, she never sent it.")])

    client = SimpleNamespace(messages=Messages())
    ClaudeChallenger(client=client).answer("Why?", {"case": {}})
    ClaudeVoice(client=client).write(Actor("lee.park", "hr_ops"), "Did she ask?", Reply("No.", "declined"))
    (kind, parse), (_kind, create) = sent
    assert parse["output_format"] is ChallengeAnswer and parse["output_config"] == {"effort": "high"} and parse["model"] == "claude-opus-5-5"
    assert create["output_config"] == {"effort": "low"} and "lee.park" in create["system"]
