"""Cases and funding as people see them: run the close, read a case's timeline, answer a question, follow the money."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import AUDIT_READ, RECONCILE_RUN, allowed, require
from app.cases import service
from app.cases.close import run_close
from app.cases.models import Case, CaseQuestion
from app.controls import posting
from app.controls.gate import check_role, enforce
from app.controls.models import FundingRequest
from app.db import get_db
from app.ledger.errors import LedgerError

router = APIRouter()


class PrecedentIn(BaseModel):
    rule: str
    scope: dict = Field(default_factory=dict)
    ruling: str | None = Field(default=None, max_length=500)


class AnswerIn(BaseModel):
    answer: str
    note: str | None = Field(default=None, max_length=500)
    precedent: PrecedentIn | None = None


class ReplyIn(BaseModel):
    text: str = Field(max_length=2000)


class ConfirmIn(BaseModel):
    accept: bool


class ChallengeIn(BaseModel):
    question: str = Field(max_length=2000)


class FundingIn(BaseModel):
    period: str
    payment_ids: list[str]
    reason: str = Field(max_length=500)
    case_ids: list[str] = Field(default_factory=list)


@router.post("/close")
def close(period: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    require(who, RECONCILE_RUN, "run the close")
    return run_close(db, period)


@router.get("/cases")
def list_cases(
    period: str | None = None, state: str | None = None, db: Session = Depends(get_db), who: Actor = Depends(current_actor)
) -> list[dict]:
    require(who, AUDIT_READ, "list cases")
    query = select(Case).order_by(Case.period, Case.kind, Case.subject_number)
    if period is not None:
        query = query.where(Case.period == period)
    if state is not None:
        query = query.where(Case.state == state)
    return [service.as_dict(row) for row in db.scalars(query)]


@router.get("/cases/{case_id}")
def get_case(case_id: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    case = _case(db, case_id)
    asked = db.scalar(select(CaseQuestion).where(CaseQuestion.case_id == case.id, CaseQuestion.asked_to == who.handle))
    if asked is None:
        require(who, AUDIT_READ, "read this case")
    return service.timeline(db, case)


@router.get("/questions/inbox")
def my_questions(db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> list[dict]:
    rows = db.scalars(
        select(CaseQuestion)
        .where(CaseQuestion.asked_to == who.handle, CaseQuestion.status == "open")
        .order_by(CaseQuestion.asked_on, CaseQuestion.id)
    )
    return [
        {"id": row.id, "case_id": row.case_id, "question": row.question, "asked_on": row.asked_on, "due_on": row.due_on}
        for row in rows
    ]


@router.post("/questions/{question_id}/answer")
def answer_question(question_id: str, body: AnswerIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    precedent = body.precedent.model_dump() if body.precedent else None
    case = service.answer(db, question_id, who, body.answer, body.note, precedent)
    return service.timeline(db, case)


@router.post("/questions/{question_id}/reply")
def reply(question_id: str, body: ReplyIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """Answer in your own words. Vestly shows how it read the reply; nothing counts until you confirm."""
    from app.cases.replies import as_dict, propose_reply

    return as_dict(propose_reply(db, question_id, who, body.text))


@router.post("/questions/replies/{draft_id}/confirm")
def confirm(draft_id: str, body: ConfirmIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    from app.cases.replies import confirm_reply

    case = confirm_reply(db, draft_id, who, body.accept)
    return service.timeline(db, case)


@router.post("/cases/{case_id}/challenge")
def challenge_case(case_id: str, body: ChallengeIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """A reviewer questions a decision; the answer comes only from that decision's record, and the decision stands."""
    from app.record.challenge import challenge

    return challenge(db, case_id, body.question, who)


@router.post("/simulate/approvers")
def simulate_approvers(period: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """Demo: the playbook's people answer what is open (controller only, like moving the clock)."""
    from app.approvers.simulate import simulate
    from app.auth.permissions import CLOCK_ADVANCE

    require(who, CLOCK_ADVANCE, "run the simulated approvers")
    return {"period": period, "actions": simulate(db, period)}


@router.post("/cases/resume")
def resume(db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    require(who, RECONCILE_RUN, "resume cases")
    return service.resume(db)


@router.post("/funding", status_code=201)
def create_funding(body: FundingIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    row = posting.request_funding(db, who, body.period, body.payment_ids, body.reason, body.case_ids)
    return posting.as_dict(db, row)


@router.get("/funding")
def list_funding(period: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> list[dict]:
    if not allowed(who, AUDIT_READ):
        enforce(check_role(who, "release_funding"), who, "read funding requests")
    rows = db.scalars(select(FundingRequest).where(FundingRequest.period == period).order_by(FundingRequest.requested_on))
    return [posting.as_dict(db, row) for row in rows]


@router.get("/funding/{request_id}/reconciliation")
def funding_reconciliation(request_id: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    if not allowed(who, AUDIT_READ):
        enforce(check_role(who, "release_funding"), who, "read a funding reconciliation")
    return posting.reconcile_funding(db, _funding(db, request_id))


@router.post("/funding/{request_id}/resend")
def resend(request_id: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """Treasury re-sends a timed-out batch. Only lines without a bank confirmation go out, under the same keys."""
    enforce(check_role(who, "release_funding"), who, "release_funding")
    return posting.send_unconfirmed(db, _funding(db, request_id), who.handle)


def _case(db: Session, case_id: str) -> Case:
    row = db.get(Case, case_id)
    if row is None:
        raise LedgerError("unknown_case", f"No case {case_id}.", 404)
    return row


def _funding(db: Session, request_id: str) -> FundingRequest:
    row = db.get(FundingRequest, request_id)
    if row is None:
        raise LedgerError("unknown_funding", f"No funding request {request_id}.", 404)
    return row
