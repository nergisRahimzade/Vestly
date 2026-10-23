from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.identities import Actor
from app.auth.permissions import JOURNAL_DRAFT, require
from app.controls.gate import check_seat, enforce
from app.ledger.errors import LedgerError
from app.ledger.chart import ENTITIES as ENTITY_ROWS
from app.ledger.models import JournalEntry, JournalLine, Period
from app.ledger.money import money_str
from app.ledger.service import (
    draft_entry,
    find_period,
    live_entry_for_key,
    parse_period,
    period_label,
    require_entity,
    withdraw_draft,
)
from app.payroll.calc import accrue_wages, accrual_days, month_length, overpaid_gross, policy_contribution, quantize
from app.periods import month_of, previous_period
from app.policy.loader import load_policy
from app.world.loader import world_loaded
from app.world.models import FundingInstruction, HrCensusRow, PayrollRegisterRow, SourceDocument

PAYROLL_PREFIX = "[Payroll]"
ZERO = Decimal("0.00")
ENTITIES = tuple(code for code, _name, _currency in ENTITY_ROWS)


def draft_payroll(session: Session, period: str, actor: Actor) -> dict:
    require(actor, JOURNAL_DRAFT, "draft payroll journals")
    enforce(check_seat(actor, "payroll"), actor, "draft payroll journals")
    parse_period(period)
    if not world_loaded(session):
        raise LedgerError("world_not_loaded", "Load the company file drops before drafting payroll.", 409)
    _require_open_books(session, period)
    _withdraw_prior_drafts(session, period, actor)
    policy = load_policy()
    notes: list[dict] = []
    people: list[dict] = []
    journals: list[JournalEntry] = []
    on_file: list[dict] = []
    hr_rows = list(session.scalars(select(HrCensusRow).where(HrCensusRow.period == period)))
    payroll_rows = list(session.scalars(select(PayrollRegisterRow).where(PayrollRegisterRow.period == period)))
    funding_rows = list(session.scalars(select(FundingInstruction).where(FundingInstruction.period == period)))
    known = {row.employee_number for row in hr_rows}

    for entity in ENTITIES:
        entity_pay = [row for row in payroll_rows if row.entity_code == entity]
        confirmed = [row for row in entity_pay if row.employee_number in known]
        ghosts = [row for row in entity_pay if row.employee_number not in known]
        for row in confirmed:
            person = _person_from_register(row, policy)
            people.append(person)
            if person["contribution_difference"] != "0.00":
                notes.append(
                    {
                        "kind": "contribution_rate_break",
                        "employee_number": row.employee_number,
                        "legal_name": row.legal_name,
                        "entity_code": entity,
                        "register_contribution": person["register_contribution"],
                        "policy_contribution": person["employer_contribution"],
                        "difference": person["contribution_difference"],
                        "ask": "payroll.provider",
                    }
                )
        for row in ghosts:
            notes.append(
                {
                    "kind": "unconfirmed_person",
                    "employee_number": row.employee_number,
                    "legal_name": row.legal_name,
                    "entity_code": entity,
                    "ask": "lee.park",
                    "note": "No wage line. Cash that already left goes to suspense.",
                }
            )
        wage = _wage_journal(session, period, entity, confirmed, people, actor, on_file)
        if wage is not None:
            journals.append(wage)
        for ghost in ghosts:
            suspense = _suspense_journal(session, period, entity, ghost, funding_rows, actor, on_file)
            if suspense is not None:
                journals.append(suspense)
        accrual = _uk_accrual_journal(session, period, entity, confirmed, hr_rows, actor, on_file)
        if accrual is not None:
            journals.append(accrual)

    journals.extend(_overpayment_journals(session, period, hr_rows, actor, notes, on_file))
    reversal = _uk_accrual_reversal(session, period, actor, on_file)
    if reversal is not None:
        journals.append(reversal)

    return {
        "period": period,
        "policy_version": policy.version,
        "policy_hash": policy.content_hash,
        "people": people,
        "notes": notes,
        "journals": journals,
        "already_on_file": on_file,
    }


def _require_open_books(session: Session, period: str) -> None:
    for entity_code in ENTITIES:
        entity = require_entity(session, entity_code)
        row = find_period(session, entity, period)
        if row is None:
            raise LedgerError("period_not_open", f"Open {entity_code} {period} before drafting payroll.", 422)
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
            JournalEntry.memo.startswith(PAYROLL_PREFIX),
        )
    )
    for entry in entries:
        withdraw_draft(session, entry, actor, "Replaced by a new payroll draft run.")
    session.commit()


def _person_from_register(row: PayrollRegisterRow, policy) -> dict:
    rate = policy.contribution_rate(row.entity_code)
    policy_amount = policy_contribution(row.gross_wages, rate)
    difference = quantize(policy_amount - row.employer_contribution)
    return {
        "employee_number": row.employee_number,
        "legal_name": row.legal_name,
        "entity_code": row.entity_code,
        "department": row.department,
        "gross_wages": money_str(row.gross_wages),
        "employee_withholdings": money_str(row.employee_withholdings),
        "net_pay": money_str(row.net_pay),
        "employer_tax": money_str(row.employer_tax),
        "register_contribution": money_str(row.employer_contribution),
        "employer_contribution": money_str(policy_amount),
        "contribution_difference": money_str(difference),
    }


def _source(session: Session, table: str, row) -> dict:
    """A pointer from a journal line back to one source row and the document it came in."""
    document = session.get(SourceDocument, row.document_id)
    return {"table": table, "id": row.id, "document": document.content_hash}


def _add(
    buckets: dict,
    account: str,
    department: str | None,
    debit: Decimal,
    credit: Decimal,
    sources: list[dict] | tuple = (),
) -> None:
    key = (account, department)
    current_debit, current_credit, known = buckets.get(key, (ZERO, ZERO, []))
    known = known + [source for source in sources if source not in known]
    buckets[key] = (current_debit + debit, current_credit + credit, known)


def _lines(buckets: dict) -> list[dict]:
    lines = []
    for (account, department), (debit, credit, sources) in sorted(
        buckets.items(), key=lambda item: (item[0][0], item[0][1] or "")
    ):
        net_debit = quantize(debit)
        net_credit = quantize(credit)
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
                "sources": sources,
            }
        )
    return lines


def _post_draft(
    session: Session,
    entity: str,
    period: str,
    memo: str,
    buckets: dict,
    actor: Actor,
    booking_key: str,
    on_file: list[dict],
) -> JournalEntry | None:
    """Draft one journal, unless a live entry in any period already books the same key.

    This period's own drafts were cleared before the run, so a live entry found here was
    approved, posted, or drafted in another month. It is reported, not booked again.
    """
    lines = _lines(buckets)
    if len(lines) < 2:
        return None
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
    policy = load_policy()
    documents = sorted({source["document"] for line in lines for source in line["sources"] if "document" in source})
    return draft_entry(
        session,
        {
            "entity_code": entity,
            "period": period,
            "memo": f"{PAYROLL_PREFIX} {memo}",
            "booking_key": booking_key,
            "provenance": {
                "documents": documents,
                "policy_version": policy.version,
                "policy_hash": policy.content_hash,
                "case_id": None,
            },
            "lines": lines,
        },
        actor,
    )


def _wage_journal(
    session: Session,
    period: str,
    entity: str,
    rows: list[PayrollRegisterRow],
    people: list[dict],
    actor: Actor,
    on_file: list[dict],
) -> JournalEntry | None:
    if not rows:
        return None
    by_number = {item["employee_number"]: item for item in people if item["entity_code"] == entity}
    buckets: dict = {}
    for row in rows:
        person = by_number[row.employee_number]
        department = row.department
        ref = [_source(session, "payroll_register_rows", row)]
        _add(buckets, "5000", department, Decimal(person["gross_wages"]), ZERO, ref)
        _add(buckets, "5100", department, Decimal(person["employer_tax"]), ZERO, ref)
        _add(buckets, "5110", department, Decimal(person["employer_contribution"]), ZERO, ref)
        _add(buckets, "2100", None, ZERO, Decimal(person["net_pay"]), ref)
        _add(buckets, "2200", None, ZERO, Decimal(person["employee_withholdings"]), ref)
        if Decimal(person["employer_tax"]) > 0:
            _add(buckets, "2210", None, ZERO, Decimal(person["employer_tax"]), ref)
        _add(buckets, "2220", None, ZERO, Decimal(person["employer_contribution"]), ref)
    return _post_draft(
        session,
        entity,
        period,
        f"{entity} wages for {month_of(period).label}",
        buckets,
        actor,
        f"payroll:wages:{entity}:{period}",
        on_file,
    )


def _suspense_journal(
    session: Session,
    period: str,
    entity: str,
    ghost: PayrollRegisterRow,
    funding: list[FundingInstruction],
    actor: Actor,
    on_file: list[dict],
) -> JournalEntry | None:
    paid = [row for row in funding if row.employee_number == ghost.employee_number and row.cleared]
    cash = sum((row.amount for row in paid), ZERO)
    if cash <= 0:
        return None
    refs = [_source(session, "payroll_register_rows", ghost)]
    refs += [_source(session, "funding_instructions", row) for row in paid]
    buckets = {}
    _add(buckets, "1210", ghost.department, cash, ZERO, refs)
    _add(buckets, "1000", None, ZERO, cash, refs)
    return _post_draft(
        session,
        entity,
        period,
        f"{entity} suspense for unconfirmed person {ghost.employee_number}",
        buckets,
        actor,
        f"payroll:suspense:{ghost.employee_number}:{period}",
        on_file,
    )


def _hr_for(hr_rows: list[HrCensusRow], employee_number: str) -> HrCensusRow | None:
    matches = [row for row in hr_rows if row.employee_number == employee_number]
    return matches[0] if matches else None


def _uk_accrual_journal(
    session: Session,
    period: str,
    entity: str,
    rows: list[PayrollRegisterRow],
    hr_rows: list[HrCensusRow],
    actor: Actor,
    on_file: list[dict],
) -> JournalEntry | None:
    if entity != "UK":
        return None
    month = month_of(period)
    buckets: dict = {}
    policy = load_policy()
    employer_tax = policy.employer_tax_rate("UK")
    for row in rows:
        days = accrual_days(row.pay_period_end, month.end)
        if days <= 0:
            continue
        hr = _hr_for(hr_rows, row.employee_number)
        monthly = (hr.annual_salary / 12) if hr is not None else Decimal(row.gross_wages)
        wages = accrue_wages(monthly, days, month_length(month.end))
        tax = quantize(wages * employer_tax)
        contrib = policy_contribution(wages, policy.contribution_rate("UK"))
        department = row.department
        refs = [_source(session, "payroll_register_rows", row)]
        if hr is not None:
            refs.append(_source(session, "hr_census_rows", hr))
        _add(buckets, "5000", department, wages, ZERO, refs)
        _add(buckets, "5100", department, tax, ZERO, refs)
        _add(buckets, "5110", department, contrib, ZERO, refs)
        _add(buckets, "2150", None, ZERO, wages, refs)
        _add(buckets, "2160", None, ZERO, tax + contrib, refs)
    return _post_draft(
        session,
        entity,
        period,
        f"UK month-end accrual for {month.label}",
        buckets,
        actor,
        f"payroll:uk_accrual:{period}",
        on_file,
    )


def _uk_accrual_reversal(session: Session, period: str, actor: Actor, on_file: list[dict]) -> JournalEntry | None:
    """Reverse last month's UK accrual, line for line. Nothing to reverse if no accrual was booked."""
    prior = previous_period(period)
    accrual = live_entry_for_key(session, f"payroll:uk_accrual:{prior}")
    if accrual is None:
        return None
    lines = session.scalars(
        select(JournalLine).where(JournalLine.entry_id == accrual.id).options(selectinload(JournalLine.account))
    )
    buckets: dict = {}
    for line in lines:
        ref = {"table": "journal_lines", "id": line.id, "journal": accrual.number}
        _add(buckets, line.account.code, line.department, line.credit, line.debit, [ref])
    return _post_draft(
        session,
        "UK",
        period,
        f"UK accrual reversal from {month_of(prior).label}, reversing {accrual.number}",
        buckets,
        actor,
        f"payroll:uk_accrual_reversal:{prior}",
        on_file,
    )


def _overpayment_journals(
    session: Session,
    period: str,
    hr_rows: list[HrCensusRow],
    actor: Actor,
    notes: list[dict],
    on_file: list[dict],
) -> list[JournalEntry]:
    """One receivable per person and per month they were paid past their HR last day.

    The key is the person and the month the pay belongs to, not the month being closed,
    so a March overpayment is booked by the first close that sees it and by no later one.
    """
    leavers = [row for row in hr_rows if row.status == "terminated" and row.termination_date]
    history = list(session.scalars(select(PayrollRegisterRow).order_by(PayrollRegisterRow.period)))
    journals: list[JournalEntry] = []
    seen: set[str] = set()
    for person in leavers:
        if person.employee_number in seen:
            continue
        seen.add(person.employee_number)
        paid = [
            row
            for row in history
            if row.employee_number == person.employee_number and row.pay_period_end > person.termination_date
        ]
        for row in paid:
            extra = overpaid_gross(row.gross_wages, row.pay_period_start, row.pay_period_end, person.termination_date)
            if extra <= 0:
                continue
            buckets: dict = {}
            refs = [_source(session, "payroll_register_rows", row), _source(session, "hr_census_rows", person)]
            _add(buckets, "1200", person.department, extra, ZERO, refs)
            _add(buckets, "5000", person.department, ZERO, extra, refs)
            entry = _post_draft(
                session,
                person.entity_code,
                period,
                f"Overpayment receivable for {person.legal_name}, {month_of(row.period).label} pay after the "
                f"HR termination date, booked in {month_of(period).label}",
                buckets,
                actor,
                f"payroll:overpayment:{person.employee_number}:{row.period}",
                on_file,
            )
            if entry is None:
                continue
            journals.append(entry)
            notes.append(
                {
                    "kind": "overpayment",
                    "employee_number": person.employee_number,
                    "legal_name": person.legal_name,
                    "entity_code": person.entity_code,
                    "gross": money_str(extra),
                    "from_period": row.period,
                    "paid_through": row.pay_period_end,
                    "termination_date": person.termination_date,
                    "ask": "lee.park",
                }
            )
    return journals
