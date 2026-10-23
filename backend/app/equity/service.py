"""The equity subledger and the monthly stock-compensation true-up.

Vestly's books start with the first loaded month. Before that, the ledger is taken to agree
with the subledger (the opening position). From then on, each close drafts the difference
between what the subledger says has been earned and what the ledger already holds, so an
error from any earlier month is corrected once, in the first open month, and never again.
"""

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth.identities import Actor
from app.auth.permissions import JOURNAL_DRAFT, LEDGER_READ, require
from app.equity.models import EquityGrantMonth, EquityScheduleRun
from app.equity.schedule import EmployeeFacts, GrantMonth, SignedGrant, as_dict, parse_signed_grant, schedule_for
from app.ledger.chart import ENTITIES as ENTITY_ROWS
from app.ledger.errors import LedgerError
from app.ledger.models import Account, JournalEntry, JournalLine, Period
from app.ledger.money import CENT, money_str
from app.ledger.service import (
    LIVE_STATUSES,
    draft_entry,
    find_period,
    live_entry_for_key,
    parse_period,
    period_label,
    require_entity,
    withdraw_draft,
)
from app.periods import add_months, month_of
from app.policy.loader import load_policy
from app.record.evidence import record
from app.world.loader import first_loaded_period, world_loaded
from app.world.models import DropLoad, EquitySnapshot, HrCensusRow, SourceDocument

EQUITY_PREFIX = "[Equity]"
ZERO = Decimal("0.00")
ENTITIES = tuple(code for code, _name, _currency in ENTITY_ROWS)


# ---------------------------------------------------------------- inputs, all from the database


def signed_grants(session: Session) -> list[SignedGrant]:
    rows = session.scalars(
        select(SourceDocument).where(SourceDocument.system == "signed_grant").order_by(SourceDocument.title)
    )
    return [parse_signed_grant(row.body, row.content_hash) for row in rows]


def employee_facts(session: Session, through_period: str) -> dict[str, EmployeeFacts]:
    """HR's view of each person using only HR files received for months up to through_period."""
    rows = list(
        session.scalars(
            select(HrCensusRow).where(HrCensusRow.period <= through_period).order_by(HrCensusRow.period)
        )
    )
    by_person: dict[str, list[HrCensusRow]] = defaultdict(list)
    for row in rows:
        by_person[row.employee_number].append(row)
    facts: dict[str, EmployeeFacts] = {}
    for number, history in by_person.items():
        latest = history[-1]
        assignments: dict[str, tuple[str, str, str]] = {}
        for row in history:
            assignments[row.period] = (row.period, row.entity_code, row.department)
        facts[number] = EmployeeFacts(
            employee_number=number,
            legal_name=latest.legal_name,
            hire_date=latest.hire_date,
            termination_date=latest.termination_date if latest.status == "terminated" else None,
            assignments=tuple(assignments[period] for period in sorted(assignments)),
        )
    return facts


def rate_cards(session: Session) -> dict[str, dict]:
    return {row.period: json.loads(row.rate_card) for row in session.scalars(select(DropLoad))}


def _inputs_hash(session: Session, through_period: str) -> str:
    hashes = session.scalars(
        select(SourceDocument.content_hash)
        .where(
            SourceDocument.system.in_(("hr", "signed_grant")),
            (SourceDocument.period <= through_period) | (SourceDocument.period == "static"),
        )
        .order_by(SourceDocument.content_hash)
    )
    cards = sorted(f"{period}:{card}" for period, card in rate_cards(session).items() if period <= through_period)
    text = "\n".join(list(hashes) + cards)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- the subledger


def compute_schedules(session: Session, through_period: str) -> tuple[list[GrantMonth], list[dict]]:
    """Build every grant's schedule in memory. Nothing is written."""
    parse_period(through_period)
    facts = employee_facts(session, through_period)
    cards = rate_cards(session)
    rows: list[GrantMonth] = []
    notes: list[dict] = []
    for grant in signed_grants(session):
        if grant.grant_date[:7] > through_period:
            continue
        person = facts.get(grant.employee_number)
        if person is None:
            notes.append(
                {
                    "kind": "grant_without_hr_record",
                    "grant_number": grant.grant_number,
                    "employee_number": grant.employee_number,
                    "legal_name": grant.legal_name,
                    "ask": "lee.park",
                }
            )
            continue
        for row in schedule_for(grant, through_period, person, cards):
            if row.cumulative_expense + CENT < row.vested_value:
                raise LedgerError(
                    "subledger_broken",
                    f"{row.grant_number} {row.period}: cumulative expense below the vested portion.",
                    500,
                )
            rows.append(row)
    return rows, notes


def rebuild_schedules(session: Session, through_period: str, actor: Actor) -> tuple[EquityScheduleRun, list[GrantMonth]]:
    """Build the subledger and keep it as a new run. Earlier runs stay as they were."""
    rows, _notes = compute_schedules(session, through_period)
    previous = session.scalar(select(func.max(EquityScheduleRun.sequence)))
    run = EquityScheduleRun(
        sequence=int(previous or 0) + 1,
        through_period=through_period,
        built_by=actor.handle,
        built_at=datetime.now(timezone.utc),
        inputs_hash=_inputs_hash(session, through_period),
    )
    session.add(run)
    session.flush()
    session.add_all(_row(run.id, row) for row in rows)
    record(
        session,
        actor.handle,
        "schedule_run",
        "equity_schedule_run",
        run.id,
        payload={"through_period": through_period, "sequence": run.sequence, "inputs_hash": run.inputs_hash, "rows": len(rows)},
    )
    session.commit()
    return run, rows


def latest_schedule_run(session: Session) -> EquityScheduleRun | None:
    return session.scalar(select(EquityScheduleRun).order_by(EquityScheduleRun.sequence.desc()).limit(1))


def schedules_as_of(session: Session, through_period: str, actor: Actor, grant_number: str | None = None) -> list[dict]:
    """A read. It computes the schedule and stores nothing."""
    require(actor, LEDGER_READ, "read equity schedules")
    rows, _notes = compute_schedules(session, through_period)
    if grant_number:
        rows = [row for row in rows if row.grant_number == grant_number]
        if not rows:
            raise LedgerError("unknown_grant", f"No signed grant {grant_number}.", 404)
    return [as_dict(row) for row in rows]


def grant_month(session: Session, period: str, employee_number: str) -> EquityGrantMonth | None:
    run = latest_schedule_run(session)
    if run is None:
        return None
    return session.scalar(
        select(EquityGrantMonth).where(
            EquityGrantMonth.run_id == run.id,
            EquityGrantMonth.period == period,
            EquityGrantMonth.employee_number == employee_number,
        )
    )


def department_subledger(session: Session, period: str, entity_code: str, department: str) -> Decimal:
    """Net expense in the entity's currency for one department and month, from the latest run."""
    run = latest_schedule_run(session)
    if run is None:
        return ZERO
    rows = session.scalars(
        select(EquityGrantMonth).where(
            EquityGrantMonth.run_id == run.id,
            EquityGrantMonth.period == period,
            EquityGrantMonth.entity_code == entity_code,
            EquityGrantMonth.department == department,
        )
    )
    return sum((row.booked_net_expense or ZERO for row in rows), ZERO)


# ---------------------------------------------------------------- the monthly draft


def draft_equity(session: Session, period: str, actor: Actor) -> dict:
    require(actor, JOURNAL_DRAFT, "draft equity journals")
    parse_period(period)
    if not world_loaded(session):
        raise LedgerError("world_not_loaded", "Load the company file drops before drafting equity.", 409)
    _require_open_books(session, period)
    _withdraw_prior_drafts(session, period, actor)
    run, rows = rebuild_schedules(session, period, actor)
    window_start = first_loaded_period(session) or period
    window = [row for row in rows if window_start <= row.period <= period]
    unpriced = sorted({(row.period, row.currency, row.booking_currency) for row in window if row.booked_net_expense is None})
    if unpriced:
        first = unpriced[0]
        raise LedgerError(
            "missing_reference_rate",
            f"No reference rate to translate {first[1]} into {first[2]} for {first[0]}. A person must supply it.",
            422,
        )

    grant_documents = {grant.grant_number: grant.content_hash for grant in signed_grants(session)}
    earned: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    sources: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in window:
        earned[(row.entity_code, row.department)] += row.booked_net_expense
    for stored in session.scalars(
        select(EquityGrantMonth)
        .where(EquityGrantMonth.run_id == run.id, EquityGrantMonth.period >= window_start, EquityGrantMonth.period <= period)
        .order_by(EquityGrantMonth.grant_number, EquityGrantMonth.period)
    ):
        sources[(stored.entity_code, stored.department)].append(
            {
                "table": "equity_grant_months",
                "id": stored.id,
                "grant": stored.grant_number,
                "period": stored.period,
                "document": grant_documents[stored.grant_number],
            }
        )
    booked, booked_lines = _booked_stock_expense(session, window_start, period)
    for key, refs in booked_lines.items():
        sources[key].extend(refs)
    provenance = {
        "documents": sorted(set(grant_documents.values())),
        "schedule_run": run.id,
        "window_start": window_start,
        "case_id": None,
    }
    on_file: list[dict] = []
    journals: list[JournalEntry] = []
    for entity in ENTITIES:
        differences = {
            department: (earned.get((code, department), ZERO) - booked.get((code, department), ZERO)).quantize(CENT)
            for (code, department) in set(earned) | set(booked)
            if code == entity
        }
        line_sources = {department: sources.get((entity, department), []) for department in differences}
        entry = _trueup_journal(session, period, entity, differences, line_sources, provenance, actor, on_file)
        if entry is not None:
            journals.append(entry)

    this_month = [row for row in rows if row.period == period]
    platform = list(session.scalars(select(EquitySnapshot).where(EquitySnapshot.period == period)))
    signed = {grant.grant_number for grant in signed_grants(session)}
    # A live true-up for an earlier month settled everything through that month, so the
    # per-grant explanation only covers the months since.
    explain_from = _month_after_last_trueup(session, period) or window_start
    explain_from = max(explain_from, window_start)
    unexplained = [row for row in window if row.period >= explain_from]
    return {
        "period": period,
        "schedule_run": run.id,
        "window_start": window_start,
        "explained_from": explain_from,
        "schedules": [as_dict(row) for row in this_month],
        "trueup": _grant_breakdown(session, unexplained, explain_from, period, signed),
        "notes": _notes(this_month, platform, signed),
        "journals": journals,
        "already_on_file": on_file,
    }


def _month_after_last_trueup(session: Session, period: str) -> str | None:
    """The month after the latest earlier month with a live true-up, if any."""
    rows = session.scalars(
        select(JournalEntry)
        .where(JournalEntry.booking_key.like("equity:trueup:%"), JournalEntry.status.in_(LIVE_STATUSES))
        .options(selectinload(JournalEntry.period))
    )
    earlier = [period_label(entry.period) for entry in rows if period_label(entry.period) < period]
    if not earlier:
        return None
    return add_months(f"{max(earlier)}-01", 1)[:7]


def _booked_stock_expense(
    session: Session, start: str, end: str
) -> tuple[dict[tuple[str, str], Decimal], dict[tuple[str, str], list[dict]]]:
    """Account 5200, net debit, per entity and department, in every live entry from start through end,
    with the ledger lines that make it up."""
    rows = session.execute(
        select(JournalEntry, JournalLine, Period)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .join(Period, Period.id == JournalEntry.period_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(Account.code == "5200", JournalEntry.status.in_(LIVE_STATUSES))
        .options(selectinload(JournalEntry.entity))
    )
    totals: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    lines: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for entry, line, period_row in rows:
        label = period_label(period_row)
        if start <= label <= end:
            key = (entry.entity.code, line.department or "")
            totals[key] += line.debit - line.credit
            lines[key].append({"table": "journal_lines", "id": line.id, "journal": entry.number})
    return totals, lines


def _grant_breakdown(
    session: Session, window: list[GrantMonth], start: str, end: str, signed: set[str]
) -> list[dict]:
    """Per grant: what the subledger earned against what the platform fed the ledger, over the window."""
    earned: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    names: dict[str, tuple[str, str]] = {}
    statuses: dict[str, set[str]] = defaultdict(set)
    approved: dict[str, bool] = {}
    for row in window:
        key = (row.grant_number, row.entity_code, row.department, row.booking_currency)
        earned[key] += row.booked_net_expense
        names[row.grant_number] = (row.employee_number, row.legal_name)
        statuses[row.grant_number].add(row.status)
        approved[row.grant_number] = row.board_approved
    hr_entity = {(row.grant_number, row.period): row.entity_code for row in window}
    fed: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    wrong_entity: set[str] = set()
    snapshots = session.scalars(
        select(EquitySnapshot).where(EquitySnapshot.period >= start, EquitySnapshot.period <= end)
    )
    for snap in snapshots:
        fed[(snap.grant_number, snap.entity_code, snap.department, snap.currency)] += snap.monthly_expense
        names.setdefault(snap.grant_number, (snap.employee_number, snap.legal_name))
        expected = hr_entity.get((snap.grant_number, snap.period))
        if expected is not None and expected != snap.entity_code:
            wrong_entity.add(snap.grant_number)
    lines = []
    for key in sorted(set(earned) | set(fed)):
        difference = (earned.get(key, ZERO) - fed.get(key, ZERO)).quantize(CENT)
        if difference == 0:
            continue
        grant_number, entity, department, currency = key
        employee_number, legal_name = names[grant_number]
        if grant_number not in signed:
            reason, ask = "platform_grant_without_signed_grant", "morgan.li"
        elif not approved.get(grant_number, True):
            reason, ask = "missing_grant_approval", "morgan.li"
        elif "forfeited" in statuses[grant_number]:
            reason, ask = "forfeiture_still_expensed", "dana.ruiz"
        elif grant_number in wrong_entity:
            reason, ask = "expense_in_wrong_entity", "dana.ruiz"
        else:
            reason, ask = "platform_differs", "morgan.li"
        lines.append(
            {
                "grant_number": grant_number,
                "employee_number": employee_number,
                "legal_name": legal_name,
                "entity_code": entity,
                "department": department,
                "currency": currency,
                "subledger": money_str(earned.get(key, ZERO)),
                "platform_fed": money_str(fed.get(key, ZERO)),
                "difference": money_str(difference),
                "reason": reason,
                "ask": ask,
            }
        )
    return lines


def _row(run_id: str, row: GrantMonth) -> EquityGrantMonth:
    return EquityGrantMonth(
        run_id=run_id,
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
        booking_currency=row.booking_currency,
        fx_rate=row.fx_rate,
        booked_net_expense=row.booked_net_expense,
    )


def _require_open_books(session: Session, period: str) -> None:
    for entity_code in ENTITIES:
        entity = require_entity(session, entity_code)
        row = find_period(session, entity, period)
        if row is None:
            raise LedgerError("period_not_open", f"Open {entity_code} {period} before drafting equity.", 422)
        if not row.accepts_entries:
            raise LedgerError("period_locked", f"{period} for {entity_code} is locked.", 409)


def _withdraw_prior_drafts(session: Session, period: str, actor: Actor) -> None:
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
    )
    for entry in entries:
        withdraw_draft(session, entry, actor, "Replaced by a new equity draft run.")
    session.commit()


def _notes(rows: list[GrantMonth], platform: list[EquitySnapshot], signed: set[str]) -> list[dict]:
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
                    "ask": "dana.ruiz",
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
        if snap is not None and snap.monthly_expense != row.booked_net_expense:
            notes.append(
                {
                    "kind": "platform_differs",
                    "grant_number": row.grant_number,
                    "employee_number": row.employee_number,
                    "legal_name": row.legal_name,
                    "subledger_net": money_str(row.booked_net_expense or ZERO),
                    "platform_expense": money_str(snap.monthly_expense),
                    "ask": "morgan.li",
                }
            )
    for snap in platform:
        if snap.grant_number not in signed:
            notes.append(
                {
                    "kind": "platform_grant_without_signed_grant",
                    "grant_number": snap.grant_number,
                    "employee_number": snap.employee_number,
                    "legal_name": snap.legal_name,
                    "ask": "morgan.li",
                }
            )
    return notes


def _trueup_journal(
    session: Session,
    period: str,
    entity: str,
    differences: dict[str, Decimal],
    line_sources: dict[str, list[dict]],
    provenance: dict,
    actor: Actor,
    on_file: list[dict],
) -> JournalEntry | None:
    buckets: dict = {}
    for department, net in differences.items():
        _add_net(buckets, department, net)
    lines = _lines(buckets)
    every_source = [source for department in differences for source in line_sources[department]]
    for line in lines:
        department = line["department"]
        line["sources"] = line_sources.get(department, []) if department is not None else every_source
    if len(lines) < 2:
        return None
    booking_key = f"equity:trueup:{entity}:{period}"
    policy = load_policy()
    live = live_entry_for_key(session, booking_key)
    if live is not None:
        on_file.append(
            {
                "booking_key": booking_key,
                "journal": live.number,
                "period": period_label(live.period),
                "status": live.status,
            }
        )
        return None
    return draft_entry(
        session,
        {
            "entity_code": entity,
            "period": period,
            "memo": f"{EQUITY_PREFIX} {entity} stock compensation true-up through {month_of(period).label}",
            "booking_key": booking_key,
            "provenance": {
                **provenance,
                "policy_version": policy.version,
                "policy_hash": policy.content_hash,
            },
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
