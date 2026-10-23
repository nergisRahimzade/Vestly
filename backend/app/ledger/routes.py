from decimal import Decimal

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import LEDGER_READ, require
from app.db import get_db
from app.ledger.errors import LedgerError
from app.ledger.models import Account, Entity, JournalEntry, Period
from app.ledger.money import money_str
from app.ledger.schemas import AccountOut, EntityOut, JournalIn, JournalOut, LineOut, PeriodOut, ReopenIn, ReviewIn
from app.ledger.service import (
    find_period,
    list_entries,
    lock_period,
    draft_entry,
    open_period,
    period_label,
    post_approved,
    reopen_period,
    require_entity,
    review_entry,
)

router = APIRouter()


def ledger_reader(who: Actor = Depends(current_actor)) -> Actor:
    require(who, LEDGER_READ, "read the books")
    return who


def period_out(row: Period) -> PeriodOut:
    return PeriodOut(
        entity_code=row.entity.code,
        period=period_label(row),
        status=row.status,
        locked_by=row.locked_by,
        reopened_by=row.reopened_by,
        reopen_reason=row.reopen_reason,
    )


def journal_out(entry: JournalEntry) -> JournalOut:
    total_debit = sum((line.debit for line in entry.lines), Decimal("0"))
    total_credit = sum((line.credit for line in entry.lines), Decimal("0"))
    return JournalOut(
        id=entry.id,
        number=entry.number,
        entity_code=entry.entity.code,
        currency=entry.currency,
        period=period_label(entry.period),
        memo=entry.memo,
        booking_key=entry.booking_key,
        status=entry.status,
        prepared_by=entry.prepared_by,
        reviewed_by=entry.reviewed_by,
        review_note=entry.review_note,
        posted_by=entry.posted_by,
        total_debit=money_str(total_debit),
        total_credit=money_str(total_credit),
        lines=[
            LineOut(
                id=line.id,
                line_number=line.line_number,
                account_code=line.account.code,
                account_name=line.account.name,
                debit=money_str(line.debit),
                credit=money_str(line.credit),
                department=line.department,
            )
            for line in entry.lines
        ],
    )


@router.get("/chart", response_model=list[AccountOut])
def chart(db: Session = Depends(get_db)) -> list[Account]:
    return list(db.scalars(select(Account).order_by(Account.code)))


@router.get("/entities", response_model=list[EntityOut])
def entities(db: Session = Depends(get_db)) -> list[Entity]:
    return list(db.scalars(select(Entity).order_by(Entity.code)))


@router.post("/entities/{entity_code}/periods/{period}", response_model=PeriodOut)
def open_entity_period(
    entity_code: str,
    period: str,
    response: Response,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> PeriodOut:
    row, created = open_period(db, entity_code, period, who)
    response.status_code = 201 if created else 200
    return period_out(row)


@router.post("/entities/{entity_code}/periods/{period}/lock", response_model=PeriodOut)
def lock_entity_period(
    entity_code: str,
    period: str,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> PeriodOut:
    return period_out(lock_period(db, entity_code, period, who))


@router.post("/entities/{entity_code}/periods/{period}/reopen", response_model=PeriodOut)
def reopen_entity_period(
    entity_code: str,
    period: str,
    body: ReopenIn,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> PeriodOut:
    """The controller reopens a closed month for a correction. Relock it when the correction posts."""
    return period_out(reopen_period(db, entity_code, period, who, body.reason))


@router.get("/entities/{entity_code}/periods/{period}", response_model=PeriodOut)
def get_entity_period(
    entity_code: str,
    period: str,
    db: Session = Depends(get_db),
    _who: Actor = Depends(ledger_reader),
) -> PeriodOut:
    entity = require_entity(db, entity_code)
    row = find_period(db, entity, period)
    if row is None:
        raise LedgerError("period_not_open", f"{entity.code} {period} has not been opened.", 404)
    return period_out(row)


@router.post("/journal-entries", response_model=JournalOut, status_code=201)
def create_journal_entry(
    body: JournalIn,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> JournalOut:
    """Create a draft. Only Vestly drafts; the controller reviews and posts."""
    return journal_out(draft_entry(db, body.model_dump(), who))


@router.post("/journal-entries/{entry_id}/approve", response_model=JournalOut)
def approve_journal_entry(
    entry_id: str,
    body: ReviewIn | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> JournalOut:
    return journal_out(review_entry(db, entry_id, who, approve=True, note=body.note if body else None))


@router.post("/journal-entries/{entry_id}/reject", response_model=JournalOut)
def reject_journal_entry(
    entry_id: str,
    body: ReviewIn,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> JournalOut:
    return journal_out(review_entry(db, entry_id, who, approve=False, note=body.note))


@router.post("/journal-entries/{entry_id}/post", response_model=JournalOut)
def post_journal_entry(
    entry_id: str,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> JournalOut:
    return journal_out(post_approved(db, entry_id, who))


@router.get("/entities/{entity_code}/journal-entries", response_model=list[JournalOut])
def entity_journal_entries(
    entity_code: str,
    period: str,
    db: Session = Depends(get_db),
    _who: Actor = Depends(ledger_reader),
) -> list[JournalOut]:
    return [journal_out(entry) for entry in list_entries(db, entity_code, period)]
