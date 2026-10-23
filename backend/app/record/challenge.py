"""Challenge mode: a reviewer questions a decision; Vestly answers only from that decision's record.

The answer may use the case, its evidence rows, its finding (sources, ranking, basis) and the state
replayed as of the decision. Code checks it before anyone reads it: every cited evidence id must be
in that record, every figure and date must appear in it, every quote must be word for word. The
decision does not change here. If the reviewer is right, the answer says so and the controller is
asked: a fix goes through the normal approval path as a new record.
"""

import json
import os

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.claims import NUMBER, QUOTE, _normalize
from app.auth.identities import CONTROLLER_HANDLE, Actor
from app.cases import service as cases
from app.cases.models import Case
from app.controls.clock import today
from app.controls.gate import check_role, enforce
from app.errors import doing
from app.ledger.errors import LedgerError
from app.reconcile.models import Reconciliation
from app.record.evidence import as_dict as event_dict
from app.record.evidence import record
from app.record.models import EvidenceEvent
from app.record.replay import replay

CHALLENGE_SYSTEM = """You are Vestly, answering a reviewer who questions one of your decisions at Northlight Labs.
Answer only from the record you are given: the case, its finding (what each source claimed, which one the policy
trusts and why), its evidence rows and the state replayed as of the decision. Cite the evidence ids you rely on.
Quote documents word for word. Do not state any figure or date that is not in the record. You cannot change the
decision here: if the reviewer is right, say so plainly and set decision_stands to false; a fix then goes to the
controller for approval. If the record does not settle the question, say what is missing."""


class ChallengeAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    decision_stands: bool
    citations: list[str]


def challenge_record(session: Session, case: Case) -> dict:
    """Everything the answer may rest on, and nothing else."""
    finding = session.get(Reconciliation, case.finding_id)
    events = list(session.scalars(select(EvidenceEvent).where(EvidenceEvent.case_id == case.id).order_by(EvidenceEvent.seq)))
    run_event = session.scalar(
        select(EvidenceEvent).where(EvidenceEvent.action == "reconciliation_run", EvidenceEvent.subject_id == finding.run_id)
    )
    if run_event is not None:
        events.insert(0, run_event)
    decided_on = max((row.sim_at for row in events if row.sim_at), default=case.opened_on)
    return {
        "case": cases.as_dict(case),
        "finding": {
            "kind": finding.kind,
            "explanation": finding.explanation,
            "trusted_value": finding.trusted_value,
            "authority_row": finding.fact,
            "ranking": json.loads(finding.ranking_json),
            "basis": json.loads(finding.basis_json),
            "policy_version": finding.policy_version,
            "policy_hash": finding.policy_hash,
        },
        "evidence": [
            {key: value for key, value in event_dict(row).items() if key in ("id", "sim_at", "actor", "action", "reason_code", "basis", "payload", "policy_hash")}
            for row in events
        ],
        "replayed": replay(session, decided_on, employee=_person(session, case), case_id=case.id),
    }


def _person(session: Session, case: Case) -> str:
    from app.world.models import FundingInstruction

    line = session.scalar(select(FundingInstruction).where(FundingInstruction.payment_id == case.subject_number))
    return line.employee_number if line is not None else case.subject_number


def problems_with(answer: ChallengeAnswer, context: dict) -> list[str]:
    ids = {row["id"] for row in context["evidence"]}
    text = _normalize(json.dumps(context, default=str, ensure_ascii=False))
    digits = text.replace(",", "")
    problems = [f"Cites {item}, which is not in this decision's record." for item in answer.citations if item not in ids]
    if not answer.citations:
        problems.append("Cites no evidence row.")
    for match in QUOTE.finditer(answer.answer):
        passage = match.group(1) or match.group(2)
        if _normalize(passage) not in text:
            problems.append(f"The quote \"{passage[:80]}\" is not in the record.")
    for number in NUMBER.findall(QUOTE.sub(" ", answer.answer)):
        plain = number.replace(",", "")
        if (len(plain) >= 3 or "." in plain) and plain not in digits:
            problems.append(f"The figure {number} is not in the record.")
    return problems


def challenge(session: Session, case_id: str, question: str, actor: Actor, challenger=None) -> dict:
    enforce(check_role(actor, "challenge"), actor, "challenge")
    case = session.get(Case, case_id)
    if case is None:
        raise LedgerError("unknown_case", f"No case {case_id}.", 404)
    question = (question or "").strip()
    if not question:
        raise LedgerError("invalid_question", "Ask a question.", 422)
    with doing("gathering the decision's record for a challenge", case=case.key):
        context = challenge_record(session, case)
    asked = record(session, actor.handle, "challenge_asked", "case", case.id, case_id=case.id, payload={"question": question}, sim_at=today(session))
    session.commit()
    challenger = challenger or default_challenger()
    try:
        answer = challenger.answer(question, context)
        problems = problems_with(answer, context)
    except Exception as exc:  # a failed model call is reported as such, with the record shown instead
        from app.errors import describe

        answer, problems = None, [f"The answer could not be produced: {describe(exc)}"]
    blocked = bool(problems)
    shown = summary_of(context) if blocked else answer.answer
    record(
        session,
        "vestly.system" if blocked else case.seat,
        "false_claim" if blocked and answer is not None else "challenge_answered",
        "case",
        case.id,
        case_id=case.id,
        reason_code="false_claim" if blocked and answer is not None else None,
        payload={
            "question_event": asked.id,
            "answer": None if answer is None else answer.model_dump(),
            "problems": problems,
            "shown": shown,
        },
        basis=[{"type": "evidence", "id": item} for item in ([] if answer is None else answer.citations)],
        sim_at=today(session),
    )
    if answer is not None and not blocked and not answer.decision_stands:
        cases.ask(
            session,
            case,
            CONTROLLER_HANDLE,
            case.seat,
            f"A reviewer ({actor.handle}) challenged {case.key} and the record supports them: {answer.answer} "
            "Approve a fix through the normal path, or answer why the decision stands.",
        )
    session.commit()
    session.refresh(case)
    return {
        "case": case.key,
        "question": question,
        "shown": shown,
        "blocked": blocked,
        "problems": problems,
        "citations": [] if answer is None else answer.citations,
        "decision_stands": True if answer is None else answer.decision_stands,
        "decision": {"state": case.state, "outcome": case.outcome},
    }


def summary_of(context: dict) -> str:
    """When Vestly's own words cannot be shown: what the record holds, built by code."""
    finding = context["finding"]
    rules = [item["id"] for item in finding["basis"] if item.get("type") == "rule"]
    precedents = [item["id"] for item in finding["basis"] if item.get("type") == "precedent"]
    lines = [
        f"The record for {context['case']['key']} (outcome {context['case']['outcome']}):",
        f"- authority row {finding['authority_row']}, trusted value {finding['trusted_value']}",
        f"- rules {', '.join(rules) or 'none'}; precedents {', '.join(precedents) or 'none'}; policy hash {finding['policy_hash']}",
        f"- evidence rows {', '.join(row['id'] for row in context['evidence'])}",
    ]
    return "\n".join(lines)


class ScriptedChallenger:
    """Answers from the record by rule: the decision, the trusted source, the rules, the precedents, the quotes."""

    name = "scripted"

    def answer(self, question: str, context: dict) -> ChallengeAnswer:
        finding, case = context["finding"], context["case"]
        rules = [item["id"] for item in finding["basis"] if item.get("type") == "rule"]
        precedents = [item for item in finding["basis"] if item.get("type") == "precedent"]
        quotes = [item for item in finding["basis"] if item.get("type") == "document" and item.get("quote")]
        parts = [f"The decision stands: {case['subject_number']} is {case['outcome']}."]
        top = finding["ranking"][0] if finding["ranking"] else None
        if top:
            parts.append(f"The policy trusts {top['source']} for {finding['authority_row']}: \"{top['reason']}\"")
        if rules:
            parts.append(f"Rules: {', '.join(rules)}.")
        for item in precedents:
            parts.append(f"Precedent {item['id']}, decided by {item['decided_by']} on {item['on']}.")
        for item in quotes[:1]:
            parts.append(f"The document reads: \"{item['quote']}\"")
        parts.append(f"Policy hash {finding['policy_hash']}.")
        citations = [row["id"] for row in context["evidence"][:3]]
        return ChallengeAnswer(answer=" ".join(parts), decision_stands=True, citations=citations)


class ClaudeChallenger:
    name = "claude"

    def __init__(self, client=None, model: str | None = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("VESTLY_MODEL", "claude-opus-5-5")

    def answer(self, question: str, context: dict) -> ChallengeAnswer:
        from app.agent.model import call_claude

        response = call_claude(
            "answering a challenge",
            self.model,
            lambda: self.client.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=CHALLENGE_SYSTEM,
                output_config={"effort": "high"},
                messages=[{"role": "user", "content": f"The record:\n{json.dumps(context, default=str)}\n\nThe reviewer asks: {question}"}],
                output_format=ChallengeAnswer,
            ),
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("The model declined to answer the challenge.")
        return response.parsed_output


def default_challenger():
    if os.environ.get("VESTLY_MODEL_MODE", "scripted") == "claude":
        return ClaudeChallenger()
    return ScriptedChallenger()

