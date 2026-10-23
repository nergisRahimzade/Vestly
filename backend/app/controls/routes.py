"""People act here: the approvals inbox, the simulated clock, call-backs, and a dry-run of the gate."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import AUDIT_READ, CALLBACK_RECORD, SOURCES_INDIVIDUAL, allowed, require
from app.controls import approvals
from app.controls.callbacks import check_pay_destination, record_callback
from app.controls.clock import advance, today
from app.controls.gate import check_role
from app.controls.models import ApprovalRequest, Callback
from app.db import get_db
from app.ledger.errors import LedgerError

router = APIRouter()


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class PrecedentIn(BaseModel):
    rule: str
    scope: dict = Field(default_factory=dict)
    ruling: str | None = Field(default=None, max_length=500)


class DeclineIn(NoteIn):
    # The controller may turn the decline into a precedent later cases follow.
    precedent: PrecedentIn | None = None


class CallbackIn(BaseModel):
    employee_number: str
    account: str
    outcome: str
    called_on: str | None = None
    note: str | None = Field(default=None, max_length=500)


class CheckIn(BaseModel):
    action: str
    employee_number: str | None = None
    account: str | None = None
    period: str | None = None
    pay_date: str | None = None


@router.get("/clock")
def get_clock(db: Session = Depends(get_db)) -> dict:
    return {"today": today(db)}


@router.post("/clock/advance")
def advance_clock(days: int = 1, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    return advance(db, who, days)


@router.get("/approvals/inbox")
def my_inbox(db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> list[dict]:
    return [approvals.as_dict(db, row) for row in approvals.inbox(db, who)]


@router.get("/approvals/{request_id}")
def get_request(request_id: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    row = db.get(ApprovalRequest, request_id)
    if row is None:
        raise LedgerError("unknown_request", f"No approval request {request_id}.", 404)
    if not (allowed(who, AUDIT_READ) or who.handle == row.requested_by or row in approvals.inbox(db, who)):
        require(who, AUDIT_READ, "read this approval request")
    return approvals.as_dict(db, row)


@router.get("/approvals")
def list_requests(
    subject_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> list[dict]:
    require(who, AUDIT_READ, "list approval requests")
    query = select(ApprovalRequest).order_by(ApprovalRequest.requested_at)
    if subject_id is not None:
        query = query.where(ApprovalRequest.subject_id == subject_id)
    if status is not None:
        query = query.where(ApprovalRequest.status == status)
    return [approvals.as_dict(db, row) for row in db.scalars(query)]


@router.post("/approvals/{request_id}/approve")
def approve_request(
    request_id: str, body: NoteIn | None = None, db: Session = Depends(get_db), who: Actor = Depends(current_actor)
) -> dict:
    row = approvals.decide(db, request_id, who, approve=True, note=body.note if body else None)
    return approvals.as_dict(db, row)


@router.post("/approvals/{request_id}/decline")
def decline_request(
    request_id: str, body: DeclineIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)
) -> dict:
    precedent = body.precedent.model_dump() if body.precedent else None
    row = approvals.decide(db, request_id, who, approve=False, note=body.note, precedent=precedent)
    return approvals.as_dict(db, row)


@router.post("/controls/callbacks", status_code=201)
def log_callback(body: CallbackIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    row = record_callback(db, who, body.employee_number, body.account, body.outcome, body.called_on, body.note)
    return _callback(row)


@router.get("/controls/callbacks")
def list_callbacks(employee_number: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> list[dict]:
    if not allowed(who, CALLBACK_RECORD):
        require(who, SOURCES_INDIVIDUAL, "read call-backs")
    rows = db.scalars(
        select(Callback).where(Callback.employee_number == employee_number).order_by(Callback.called_on)
    )
    return [_callback(row) for row in rows]


@router.post("/controls/check")
def check(body: CheckIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """Ask the gate before acting. Vestly's tools use this so a refusal comes back with its rule id."""
    verdict = check_role(who, body.action)
    if verdict.allowed and body.action == "request_funding" and body.account and body.employee_number:
        if not (body.period and body.pay_date):
            raise LedgerError("invalid_check", "A funding check needs the period and the pay date.", 422)
        verdict = check_pay_destination(db, body.employee_number, body.account, body.period, body.pay_date)
    return verdict.as_dict()


def _callback(row: Callback) -> dict:
    return {
        "id": row.id,
        "employee_number": row.employee_number,
        "account": row.account,
        "outcome": row.outcome,
        "called_by": row.called_by,
        "called_on": row.called_on,
        "note": row.note,
    }
