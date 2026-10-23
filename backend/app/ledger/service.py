from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.auth.identities import Actor
from app.auth.permissions import JOURNAL_DRAFT, JOURNAL_POST, JOURNAL_REVIEW, PERIOD_LOCK, PERIOD_OPEN, require
from app.ledger.chart import DEPARTMENTS
from app.ledger.errors import LedgerError
from app.ledger.models import Account, Entity, JournalEntry, JournalLine, Period
from app.ledger.money import parse_money


@dataclass(frozen=True)
class LineDraft:
    account: Account
    debit: Decimal
    credit: Decimal
    department: str | None


def parse_period(period: str) -> tuple[int, int]:
    try:
        year_text, month_text = period.split("-")
        year, month = int(year_text), int(month_text)
    except ValueError:
        raise LedgerError("invalid_period", "Period must be YYYY-MM.", 422) from None
    if year < 2000 or month < 1 or month > 12 or f"{year:04d}-{month:02d}" != period:
        raise LedgerError("invalid_period", "Period must be YYYY-MM.", 422)
    return year, month


def period_label(period: Period) -> str:
    return f"{period.year:04d}-{period.month:02d}"


def require_entity(session: Session, entity_code: str) -> Entity:
    code = entity_code.upper()
    entity = session.scalar(select(Entity).where(Entity.code == code))
    if entity is None:
        raise LedgerError("unknown_entity", f"Entity {code} is not in Northlight.", 404)
    return entity


def find_period(session: Session, entity: Entity, period: str) -> Period | None:
    year, month = parse_period(period)
    return session.scalar(
        select(Period).where(Period.entity_id == entity.id, Period.year == year, Period.month == month)
    )


def open_period(session: Session, entity_code: str, period: str, actor: Actor) -> tuple[Period, bool]:
    require(actor, PERIOD_OPEN, "open a period")
    entity = require_entity(session, entity_code)
    existing = find_period(session, entity, period)
    if existing is not None:
        if existing.status != "open":
            raise LedgerError("period_locked", f"{period} for {entity.code} is locked.", 409)
        return existing, False
    year, month = parse_period(period)
    row = Period(entity=entity, year=year, month=month, status="open")
    session.add(row)
    session.commit()
    return row, True


def lock_period(session: Session, entity_code: str, period: str, actor: Actor) -> Period:
    require(actor, PERIOD_LOCK, "lock a period")
    entity = require_entity(session, entity_code)
    row = find_period(session, entity, period)
    if row is None:
        raise LedgerError("period_not_open", f"Open {entity.code} {period} before locking it.", 404)
    if row.status == "locked":
        return row
    row.status = "locked"
    row.locked_by = actor.handle
    row.locked_at = datetime.now(timezone.utc)
    session.commit()
    return row


def _line_drafts(session: Session, lines: list[dict]) -> list[LineDraft]:
    if len(lines) < 2:
        raise LedgerError("unbalanced_journal", "A journal entry needs at least two lines.", 422)
    drafts: list[LineDraft] = []
    for line in lines:
        code = str(line["account_code"])
        account = session.scalar(select(Account).where(Account.code == code))
        if account is None:
            raise LedgerError("unknown_account", f"Account {code} is not on the chart.", 422)
        debit = parse_money(str(line["debit"]))
        credit = parse_money(str(line["credit"]))
        if (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
            raise LedgerError(
                "invalid_line",
                "Each line needs a debit or a credit, not both and not neither.",
                422,
            )
        department = line.get("department")
        if department is not None and department not in DEPARTMENTS:
            names = ", ".join(DEPARTMENTS)
            raise LedgerError("unknown_department", f"Department must be one of {names}.", 422)
        drafts.append(LineDraft(account=account, debit=debit, credit=credit, department=department))
    return drafts


def _require_balance(drafts: list[LineDraft]) -> tuple[Decimal, Decimal]:
    debits = sum((line.debit for line in drafts), Decimal("0.00"))
    credits = sum((line.credit for line in drafts), Decimal("0.00"))
    if debits != credits or debits <= 0:
        raise LedgerError(
            "unbalanced_journal",
            f"Debits {debits:.2f} do not equal credits {credits:.2f}.",
            422,
        )
    return debits, credits


def _translate_integrity(exc: IntegrityError, entity_code: str, period: str) -> LedgerError:
    message = str(exc.orig).lower()
    if "period_locked" in message:
        return LedgerError("period_locked", f"{period} for {entity_code} is locked.", 409)
    if "unbalanced_journal" in message:
        return LedgerError("unbalanced_journal", "The journal entry does not balance.", 422)
    if "preparer_is_not_reviewer" in message:
        return LedgerError("separation_of_duties", "The preparer cannot review their own entry.", 403)
    if "journal_is_final" in message:
        return LedgerError("journal_is_final", "This journal can no longer change.", 409)
    return LedgerError("ledger_rejected", "The database rejected the journal entry.", 422)


def _commit(session: Session, entity_code: str, period: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _translate_integrity(exc, entity_code, period) from exc


def _open_period_for(session: Session, entity: Entity, period: str, verb: str) -> Period:
    period_row = find_period(session, entity, period)
    if period_row is None:
        raise LedgerError("period_not_open", f"Open {entity.code} {period} before {verb}.", 422)
    if period_row.status != "open":
        raise LedgerError("period_locked", f"{period} for {entity.code} is locked.", 409)
    return period_row


def _build_entry(session: Session, payload: dict, prepared_by: str) -> tuple[JournalEntry, Entity, str]:
    entity = require_entity(session, str(payload["entity_code"]))
    period = str(payload["period"])
    period_row = _open_period_for(session, entity, period, "drafting")
    memo = str(payload["memo"]).strip()
    if not memo:
        raise LedgerError("invalid_memo", "A journal entry needs a memo.", 422)
    drafts = _line_drafts(session, list(payload["lines"]))
    _require_balance(drafts)
    existing = session.scalar(
        select(func.count())
        .select_from(JournalEntry)
        .where(JournalEntry.entity_id == entity.id, JournalEntry.period_id == period_row.id)
    )
    entry = JournalEntry(
        entity=entity,
        period=period_row,
        number=f"JE-{entity.code}-{period}-{int(existing or 0) + 1:04d}",
        memo=memo,
        status="draft",
        currency=entity.currency,
        prepared_by=prepared_by,
        prepared_at=datetime.now(timezone.utc),
        lines=[
            JournalLine(
                account=draft.account,
                line_number=index,
                debit=draft.debit,
                credit=draft.credit,
                department=draft.department,
            )
            for index, draft in enumerate(drafts, start=1)
        ],
    )
    return entry, entity, period


def draft_entry(session: Session, payload: dict, actor: Actor) -> JournalEntry:
    """Vestly prepares. A draft must already balance and sit in an open month."""
    require(actor, JOURNAL_DRAFT, "draft a journal entry")
    entry, entity, period = _build_entry(session, payload, actor.handle)
    session.add(entry)
    _commit(session, entity.code, period)
    return entry


def require_entry(session: Session, entry_id: str) -> JournalEntry:
    entry = session.get(JournalEntry, entry_id)
    if entry is None:
        raise LedgerError("unknown_journal", f"Journal {entry_id} does not exist.", 404)
    return entry


def review_entry(session: Session, entry_id: str, actor: Actor, approve: bool, note: str | None) -> JournalEntry:
    require(actor, JOURNAL_REVIEW, "review a journal entry")
    entry = require_entry(session, entry_id)
    if entry.status != "draft":
        raise LedgerError("journal_is_final", f"{entry.number} is {entry.status}, not a draft.", 409)
    if entry.prepared_by == actor.handle:
        raise LedgerError("separation_of_duties", "The preparer cannot review their own entry.", 403)
    if not approve and not (note or "").strip():
        raise LedgerError("invalid_note", "A rejection needs a reason.", 422)
    period = period_label(entry.period)
    if approve:
        _open_period_for(session, entry.entity, period, "approving")
    entry.status = "approved" if approve else "rejected"
    entry.reviewed_by = actor.handle
    entry.reviewed_at = datetime.now(timezone.utc)
    entry.review_note = (note or "").strip() or None
    _commit(session, entry.entity.code, period)
    return entry


def post_approved(session: Session, entry_id: str, actor: Actor) -> JournalEntry:
    require(actor, JOURNAL_POST, "post a journal entry")
    entry = require_entry(session, entry_id)
    if entry.status != "approved":
        raise LedgerError("not_approved", f"{entry.number} is {entry.status}. Only an approved entry posts.", 409)
    period = period_label(entry.period)
    _open_period_for(session, entry.entity, period, "posting")
    entry.status = "posted"
    entry.posted_by = actor.handle
    entry.posted_at = datetime.now(timezone.utc)
    _commit(session, entry.entity.code, period)
    return entry


def import_posted_entry(session: Session, payload: dict, actor: Actor, source: str) -> JournalEntry:
    """Bring in an entry that was already on the books before Vestly, recorded as prepared by its source system."""
    require(actor, JOURNAL_POST, "import posted history")
    entry, entity, period = _build_entry(session, payload, source)
    now = datetime.now(timezone.utc)
    entry.status = "posted"
    entry.reviewed_by = actor.handle
    entry.reviewed_at = now
    entry.posted_by = actor.handle
    entry.posted_at = now
    session.add(entry)
    _commit(session, entity.code, period)
    return entry


def list_entries(session: Session, entity_code: str, period: str) -> list[JournalEntry]:
    entity = require_entity(session, entity_code)
    year, month = parse_period(period)
    return list(
        session.scalars(
            select(JournalEntry)
            .join(Period)
            .where(JournalEntry.entity_id == entity.id, Period.year == year, Period.month == month)
            .options(
                selectinload(JournalEntry.lines).selectinload(JournalLine.account),
                selectinload(JournalEntry.entity),
                selectinload(JournalEntry.period),
            )
            .order_by(JournalEntry.number)
        )
    )
