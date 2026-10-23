"""Free-text replies: a person writes in their own words; Vestly reads it into a structured answer;
the same person confirms the reading; only then does it count as their answer.

The reading is checked against their words first (the quote and any corrected value must be
in the reply). A reading that does not hold up is never put to them as fact: they are asked
to answer with the plain options instead.

How a reading becomes an answer:
  confirmed -> confirmed
  partial   -> partial, with the note (the case asks a follow-up)
  declined  -> declined, with the note (the case is held)
  corrected -> declined, noting the corrected value: the source holds a wrong value and must be
               fixed at the source before anything moves
"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agent.extract import interpretation_problems, reader
from app.auth.identities import Actor
from app.cases import service
from app.cases.models import Case, CaseQuestion, ReplyDraft
from app.controls.clock import today
from app.controls.gate import check_role, deny, enforce
from app.ledger.errors import LedgerError
from app.record.evidence import record

TO_ANSWER = {"confirmed": "confirmed", "partial": "partial", "declined": "declined", "corrected": "declined"}


def propose_reply(session: Session, question_id: str, actor: Actor, text: str) -> ReplyDraft:
    question = _question(session, question_id)
    enforce(check_role(actor, "answer"), actor, "answer")
    if question.asked_to != actor.handle:
        enforce(deny("ACC-1", f"The question was asked of {question.asked_to}, not {actor.handle}."), actor, "answer")
    if question.status != "open":
        raise LedgerError("question_closed", f"This question is {question.status}.", 409)
    text = (text or "").strip()
    if not text:
        raise LedgerError("invalid_reply", "Write a reply.", 422)
    interpretation = reader().interpret(question.question, text)
    problems = interpretation_problems(text, interpretation)
    draft = ReplyDraft(
        question_id=question.id,
        replied_by=actor.handle,
        reply=text,
        interpretation_json=json.dumps(interpretation.model_dump()),
        problems_json=json.dumps(problems),
        status="unreadable" if problems else "proposed",
        created_at=datetime.now(timezone.utc),
    )
    session.add(draft)
    session.flush()
    record(
        session,
        actor.handle,
        "reply_read",
        "question",
        question.id,
        case_id=question.case_id,
        payload={"draft": draft.id, "reply": text, "reading": interpretation.model_dump(), "problems": problems, "status": draft.status},
        sim_at=today(session),
    )
    session.commit()
    return draft


def confirm_reply(session: Session, draft_id: str, actor: Actor, accept: bool) -> Case:
    """The person who wrote the reply says whether Vestly read it right. Only a confirmed reading counts."""
    draft = session.get(ReplyDraft, draft_id)
    if draft is None:
        raise LedgerError("unknown_reply", f"No reply {draft_id}.", 404)
    if draft.replied_by != actor.handle:
        enforce(deny("ACC-1", f"Only {draft.replied_by} can confirm how their reply was read."), actor, "answer")
    if draft.status != "proposed":
        raise LedgerError("reply_closed", f"This reply is {draft.status}; it cannot be confirmed.", 409)
    question = _question(session, draft.question_id)
    reading = json.loads(draft.interpretation_json)
    if not accept:
        draft.status = "rejected"
        session.flush()
        record(session, actor.handle, "reply_reading_rejected", "question", question.id, case_id=question.case_id, payload={"draft": draft.id}, sim_at=today(session))
        session.commit()
        return session.get(Case, question.case_id)
    draft.status = "confirmed"
    session.flush()
    record(session, actor.handle, "reply_reading_confirmed", "question", question.id, case_id=question.case_id, payload={"draft": draft.id, "reading": reading}, sim_at=today(session))
    session.commit()
    note = reading["note"]
    if reading["answer"] == "corrected":
        note = f"Corrected value: {reading['corrected_value']}. {note}"
    note = f'{note} (their words: "{reading["quote"]}")'
    return service.answer(session, question.id, actor, TO_ANSWER[reading["answer"]], note if reading["answer"] != "confirmed" else None)


def as_dict(draft: ReplyDraft) -> dict:
    return {
        "id": draft.id,
        "question_id": draft.question_id,
        "replied_by": draft.replied_by,
        "reply": draft.reply,
        "reading": json.loads(draft.interpretation_json) if draft.interpretation_json else None,
        "counts_as": None if not draft.interpretation_json else TO_ANSWER[json.loads(draft.interpretation_json)["answer"]],
        "problems": json.loads(draft.problems_json),
        "status": draft.status,
    }


def _question(session: Session, question_id: str) -> CaseQuestion:
    question = session.get(CaseQuestion, question_id)
    if question is None:
        raise LedgerError("unknown_question", f"No question {question_id}.", 404)
    return question
