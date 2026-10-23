from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.auth.identities import Actor
from app.auth.permissions import JOURNAL_DRAFT, LEDGER_READ, require
from app.equity.models import EquityGrantMonth
from app.equity.schedule import GrantMonth, as_dict, load_signed_grants, schedule_for
from app.ledger.errors import LedgerError
from app.ledger.models import JournalEntry, Period
from app.ledger.money import CENT, money_str
from app.ledger.service import draft_entry, find_period, parse_period, require_entity
from app.world.drops import ENTITIES, grants_dir, month_of
from app.world.loader import WORLD_VERSION
from app.world.models import EquitySnapshot, WorldLoad

EQUITY_PREFIX = "[Equity]"
ZERO = Decimal("0.00")


def rebuild_schedules(session: Session, through_period: str) -> list[GrantMonth]:
    """Rebuild every grant through this month from the signed grant and true HR dates."""
    parse_period(through_period)
    session.execute(delete(EquityGrantMonth))
    rows: list[GrantMonth] = []
    for grant in load_signed_grants(grants_dir()):
        for row in schedule_for(grant, through_period):
            if row.cumulative_expense + CENT < row.vested_value:
                raise LedgerError(
                    "subledger_broken",
                    f"{row.grant_number} {row.period}: cumulative expense below the vested portion.",
                    500,
                )
            session.add(_row(row))
            rows.append(row)
    session.commit()
    return rows


def schedules_as_of(session: Session, through_period: str, actor: Actor, grant_number: str | None = None) -> list[dict]:
    require(actor, LEDGER_READ, "read equity schedules")
    rows = rebuild_schedules(session, through_period)
    if grant_number:
        rows = [row for row in rows if row.grant_number == grant_number]
        if not rows:
            raise LedgerError("unknown_grant", f"No signed grant {grant_number}.", 404)
    return [as_dict(row) for row in rows]


def draft_equity(session: Session, period: str, actor: Actor) -> dict:
    require(actor, JOURNAL_DRAFT, "draft equity journals")
    parse_period(period)
    if session.get(WorldLoad, WORLD_VERSION) is None:
        raise LedgerError("world_not_loaded", "Load the company file drops before drafting equity.", 409)
    _require_open_books(session, period)
    _clear_prior_drafts(session, period, actor)
    rows = rebuild_schedules(session, period)
    this_month = [row for row in rows if row.period == period]
    platform = list(session.scalars(select(EquitySnapshot).where(EquitySnapshot.period == period)))
    notes = _notes(this_month, platform)
    journals: list[JournalEntry] = []
    for entity in ENTITIES:
        entry = _entity_journal(session, period, entity, this_month, actor)
        if entry is not None:
            journals.append(entry)
    return {
        "period": period,
        "schedules": [as_dict(row) for row in this_month],
        "notes": notes,
        "journals": journals,
    }


def grant_month(session: Session, period: str, employee_number: str) -> EquityGrantMonth | None:
    return session.scalar(
        select(EquityGrantMonth).where(
            EquityGrantMonth.period == period,
            EquityGrantMonth.employee_number == employee_number,
        )
    )


def _row(row: GrantMonth) -> EquityGrantMonth:
    return EquityGrantMonth(
        grant_number=row.grant_number,
        employee_number=row.employee_number,
        legal_name=row.legal_name,
        period=row.period,
        entity_code=row.entity_code,
        department=row.department,
        currency=row.currency,
        service_month=row.service_month,
        monthly_expense=row.monthly_expense,
        reversal=row.reversal,
        net_expense=row.net_expense,
        cumulative_expense=row.cumulative_expense,
        vested_shares=row.vested_shares,
        vested_value=row.vested_value,
        status=row.status,
        board_approved=row.board_approved,
        vest_end=row.vest_end,
        cliff_end=row.cliff_end,
    )


def _require_open_books(session: Session, period: str) -> None:
    for entity_code in ENTITIES:
        entity = require_entity(session, entity_code)
        row = find_period(session, entity, period)
        if row is None:
            raise LedgerError("period_not_open", f"Open {entity_code} {period} before drafting equity.", 422)
        if row.status != "open":
            raise LedgerError("period_locked", f"{period} for {entity_code} is locked.", 409)


def _clear_prior_drafts(session: Session, period: str, actor: Actor) -> None:
    year, month = parse_period(period)
    entries = session.scalars(
        select(JournalEntry)
        .join(Period)
        .where(
            Period.year == year,
            Period.month == month,
            JournalEntry.prepared_by == actor.handle,
            JournalEntry.status == "draft",
            JournalEntry.memo.startswith(EQUITY_PREFIX),
        )
        .options(selectinload(JournalEntry.lines))
    )
    for entry in entries:
        session.delete(entry)
    session.commit()


def _notes(rows: list[GrantMonth], platform: list[EquitySnapshot]) -> list[dict]:
    by_grant = {row.grant_number: row for row in platform}
    notes: list[dict] = []
    for row in rows:
        snap = by_grant.get(row.grant_number)
        if not row.board_approved:
            notes.append(
                {
                    "kind": "missing_grant_approval",
                    "grant_number": row.grant_number,
                    "employee_number": row.employee_number,
                    "legal_name": row.legal_name,
                    "ask": "morgan.li",
                    "note": "No expense without a board approval on the signed grant.",
                }
            )
        if row.status == "forfeited" and row.reversal > 0:
            notes.append(
                {
                    "kind": "forfeiture",
                    "grant_number": row.grant_number,
                    "employee_number": row.employee_number,
                    "legal_name": row.legal_name,
                    "reversal": money_str(row.reversal),
                    "ask": "lee.park",
                }
            )
        if snap is not None and snap.vest_end != row.vest_end:
            notes.append(
                {
                    "kind": "vest_end_mismatch",
                    "grant_number": row.grant_number,
                    "employee_number": row.employee_number,
                    "legal_name": row.legal_name,
                    "signed_vest_end": row.vest_end,
                    "platform_vest_end": snap.vest_end,
                    "ask": "morgan.li",
                }
            )
        if snap is not None and snap.monthly_expense != row.net_expense:
            notes.append(
                {
                    "kind": "platform_differs",
                    "grant_number": row.grant_number,
                    "employee_number": row.employee_number,
                    "legal_name": row.legal_name,
                    "subledger_net": money_str(row.net_expense),
                    "platform_expense": money_str(snap.monthly_expense),
                    "ask": "morgan.li",
                }
            )
    return notes


def _entity_journal(
    session: Session,
    period: str,
    entity: str,
    rows: list[GrantMonth],
    actor: Actor,
) -> JournalEntry | None:
    buckets: dict = {}
    for row in [item for item in rows if item.entity_code == entity]:
        _add_net(buckets, row.department, row.net_expense)
    lines = _lines(buckets)
    if len(lines) < 2:
        return None
    return draft_entry(
        session,
        {
            "entity_code": entity,
            "period": period,
            "memo": f"{EQUITY_PREFIX} {entity} stock compensation for {month_of(period).label}",
            "lines": lines,
        },
        actor,
    )


def _add_net(buckets: dict, department: str, net: Decimal) -> None:
    if net > 0:
        _add(buckets, "5200", department, net, ZERO)
        _add(buckets, "3000", None, ZERO, net)
    elif net < 0:
        amount = -net
        _add(buckets, "5200", department, ZERO, amount)
        _add(buckets, "3000", None, amount, ZERO)


def _add(buckets: dict, account: str, department: str | None, debit: Decimal, credit: Decimal) -> None:
    key = (account, department)
    current_debit, current_credit = buckets.get(key, (ZERO, ZERO))
    buckets[key] = (current_debit + debit, current_credit + credit)


def _lines(buckets: dict) -> list[dict]:
    lines = []
    for (account, department), (debit, credit) in sorted(
        buckets.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        net_debit = debit.quantize(CENT)
        net_credit = credit.quantize(CENT)
        if net_debit > 0 and net_credit > 0:
            if net_debit >= net_credit:
                net_debit, net_credit = net_debit - net_credit, ZERO
            else:
                net_credit, net_debit = net_credit - net_debit, ZERO
        if net_debit == 0 and net_credit == 0:
            continue
        lines.append(
            {
                "account_code": account,
                "department": department,
                "debit": money_str(net_debit),
                "credit": money_str(net_credit),
            }
        )
    return lines
