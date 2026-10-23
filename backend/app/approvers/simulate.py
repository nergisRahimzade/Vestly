"""Simulated approvers: HR, the controller, treasury, legal, the provider and department heads, played by script or by Claude.

They act only through the doors a person uses: a free-text reply that Vestly reads, checks against their
words and shows back (they confirm only a reading that matches what they meant), the approvals inbox
under their own identity, and HR's call-back log. A playbook says what each person would answer; anything
it does not cover stays open, answerable by hand. Scripted for tests and recordings; with Claude, each
person writes their reply in their own words from the playbook's intent.
"""

import json
import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identities import IDENTITIES, Actor
from app.cases.models import Case, CaseQuestion
from app.cases.replies import confirm_reply, propose_reply
from app.controls import approvals
from app.controls.callbacks import record_callback
from app.controls.models import ApprovalRequest
from app.errors import doing
from app.ledger.errors import LedgerError

ROLE_OF = {handle: role for handle, _name, role, _dept in IDENTITIES}


@dataclass(frozen=True)
class Reply:
    text: str
    # what the person means: confirmed, partial, declined or corrected
    meaning: str
    # an HR call-back to log first: (account, outcome)
    callback: tuple[str, str] | None = None


@dataclass(frozen=True)
class Decision:
    approve: bool
    note: str


# (case kind, who is asked) -> what they answer. The scenario: Maya never sent the email; Priya's exit is right.
DEFAULT_REPLIES: dict[tuple[str, str], Reply] = {
    ("bank_detail_change", "lee.park"): Reply(
        "No, she never sent that email. I called her on the number we have on file and she knew nothing about it. Block the account.",
        "declined",
        callback=("9911882277", "denied"),
    ),
    ("late_termination", "lee.park"): Reply("Yes, that is right. She left on 12 March; HR keyed it late.", "confirmed"),
    ("contribution_rate_break", "payroll.provider"): Reply("We fixed the rate for future runs only, the back months are still pending.", "partial"),
    ("missing_grant_approval", "morgan.li"): Reply("That grant was not approved by the board.", "declined"),
    ("vest_schedule_mismatch", "morgan.li"): Reply("Confirmed, the signed grant is right and the platform will be corrected.", "confirmed"),
    ("duplicate_grant", "morgan.li"): Reply("Confirmed, it is the same grant entered twice.", "confirmed"),
    ("uncleared_funding", "alex.moreau"): Reply("Confirmed, those two lines never left; please re-send them.", "confirmed"),
}
# Approvals the scenario's people give: treasury releases funding; the finance manager approves drafts under the limit.
DEFAULT_APPROVALS = {
    ("funding_request", "treasury"): Decision(True, "Released after checking the bank file."),
}


def simulate(session: Session, period: str, replies=None, approvals_policy=None, voice=None) -> list[dict]:
    """Let the playbook's people answer what is open for a month. Returns what each did, in order."""
    replies = DEFAULT_REPLIES if replies is None else replies
    approvals_policy = DEFAULT_APPROVALS if approvals_policy is None else approvals_policy
    log: list[dict] = []
    questions = session.scalars(
        select(CaseQuestion).join(Case, Case.id == CaseQuestion.case_id).where(Case.period == period, CaseQuestion.status == "open").order_by(CaseQuestion.asked_on, CaseQuestion.id)
    )
    for question in list(questions):
        case = session.get(Case, question.case_id)
        plan = replies.get((case.kind, question.asked_to))
        if plan is None:
            continue
        person = Actor(question.asked_to, ROLE_OF[question.asked_to])
        with doing("a simulated person answering", person=person.handle, case=case.key):
            if plan.callback is not None:
                account, outcome = plan.callback
                record_callback(session, person, _person_of(session, case), account, outcome, None, plan.text[:200])
            text = voice.write(person, question.question, plan) if voice is not None else plan.text
            draft = propose_reply(session, question.id, person, text)
            reading = json.loads(draft.interpretation_json) if draft.interpretation_json else None
            matches = draft.status == "proposed" and reading["answer"] == plan.meaning
            if draft.status == "proposed":
                confirm_reply(session, draft.id, person, accept=matches)
        log.append({"person": person.handle, "case": case.key, "said": text, "read_as": None if not reading else reading["answer"], "counted": matches})
    for request in list(session.scalars(select(ApprovalRequest).where(ApprovalRequest.period == period, ApprovalRequest.status == "pending"))):
        for role in json.loads(request.inbox_roles):
            plan = approvals_policy.get((request.kind, role))
            if plan is None:
                continue
            handle = next(handle for handle, _name, item, _dept in IDENTITIES if item == role)
            try:
                approvals.decide(session, request.id, Actor(handle, role), plan.approve, plan.note)
                log.append({"person": handle, "approval": request.id, "approved": plan.approve})
            except LedgerError as exc:
                log.append({"person": handle, "approval": request.id, "refused": exc.rule or exc.code})
            break
    return log


def _person_of(session: Session, case: Case) -> str:
    from app.world.models import FundingInstruction

    line = session.scalar(select(FundingInstruction).where(FundingInstruction.payment_id == case.subject_number))
    return line.employee_number if line is not None else case.subject_number


class ClaudeVoice:
    """The person writes their reply in their own words, from what they mean; Vestly still has to read it right."""

    def __init__(self, client=None, model: str | None = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("VESTLY_MODEL", "claude-opus-5-5")

    def write(self, person: Actor, question: str, plan: Reply) -> str:
        from app.agent.model import call_claude

        response = call_claude(
            "a simulated person writing a reply",
            self.model,
            lambda: self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                output_config={"effort": "low"},
                system=(
                    f"You are {person.handle} ({person.role}) at Northlight Labs, replying to a question from Vestly, the finance agent. "
                    "Write two or three plain sentences in your own words. Keep every fact and figure you are given exactly; add none."
                ),
                messages=[{"role": "user", "content": f"Vestly asked: {question}\n\nWhat you mean ({plan.meaning}): {plan.text}"}],
            ),
        )
        return "".join(block.text for block in response.content if block.type == "text").strip()
