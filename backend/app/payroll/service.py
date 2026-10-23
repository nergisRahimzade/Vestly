from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.identities import Actor
from app.auth.permissions import JOURNAL_DRAFT, require
from app.ledger.errors import LedgerError
from app.ledger.models import JournalEntry, JournalLine, Period
from app.ledger.money import money_str
from app.ledger.service import draft_entry, find_period, parse_period, require_entity
from app.payroll.calc import accrue_wages, accrual_days, month_length, overpaid_gross, policy_contribution, quantize
from app.policy.loader import load_policy
from app.world.drops import ENTITIES, ENTITY_RATES, month_of
from app.world.loader import WORLD_VERSION
from app.world.models import FundingInstruction, HrCensusRow, PayrollRegisterRow, WorldLoad

PAYROLL_PREFIX = "[Payroll]"
ZERO = Decimal("0.00")


def previous_period(period: str) -> str:
    year, month = parse_period(period)
    if month == 1:
        return f"{year - 1:04d}-12"
    return f"{year:04d}-{month - 1:02d}"


def draft_payroll(session: Session, period: str, actor: Actor) -> dict:
    require(actor, JOURNAL_DRAFT, "draft payroll journals")
    parse_period(period)
    if session.get(WorldLoad, WORLD_VERSION) is None:
        raise LedgerError("world_not_loaded", "Load the company file drops before drafting payroll.", 409)
    _require_open_books(session, period)
    _clear_prior_drafts(session, period, actor)
    policy = load_policy()
    notes: list[dict] = []
    people: list[dict] = []
    journals: list[JournalEntry] = []
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
        wage = _wage_journal(session, period, entity, confirmed, people, actor)
        if wage is not None:
            journals.append(wage)
        for ghost in ghosts:
            suspense = _suspense_journal(session, period, entity, ghost, funding_rows, actor)
            if suspense is not None:
                journals.append(suspense)
        accrual = _uk_accrual_journal(session, period, entity, confirmed, hr_rows, actor)
        if accrual is not None:
            journals.append(accrual)

    overpay = _overpayment_journal(session, period, hr_rows, actor, notes)
    if overpay is not None:
        journals.append(overpay)
    reversal = _uk_accrual_reversal(session, period, actor)
    if reversal is not None:
        journals.append(reversal)

    return {
        "period": period,
        "policy_version": policy.version,
        "policy_hash": policy.content_hash,
        "people": people,
        "notes": notes,
        "journals": journals,
    }


def _require_open_books(session: Session, period: str) -> None:
    for entity_code in ENTITIES:
        entity = require_entity(session, entity_code)
        row = find_period(session, entity, period)
        if row is None:
            raise LedgerError("period_not_open", f"Open {entity_code} {period} before drafting payroll.", 422)
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
            JournalEntry.memo.startswith(PAYROLL_PREFIX),
        )
        .options(selectinload(JournalEntry.lines))
    )
    for entry in entries:
        session.delete(entry)
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


def _add(buckets: dict, account: str, department: str | None, debit: Decimal, credit: Decimal) -> None:
    key = (account, department)
    current_debit, current_credit = buckets.get(key, (ZERO, ZERO))
    buckets[key] = (current_debit + debit, current_credit + credit)


def _lines(buckets: dict) -> list[dict]:
    lines = []
    for (account, department), (debit, credit) in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1] or "")):
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
                "department": department if net_debit > 0 else department,
                "debit": money_str(net_debit),
                "credit": money_str(net_credit),
            }
        )
    return lines


def _post_draft(session: Session, entity: str, period: str, memo: str, buckets: dict, actor: Actor) -> JournalEntry | None:
    lines = _lines(buckets)
    if len(lines) < 2:
        return None
    return draft_entry(
        session,
        {"entity_code": entity, "period": period, "memo": f"{PAYROLL_PREFIX} {memo}", "lines": lines},
        actor,
    )


def _wage_journal(
    session: Session,
    period: str,
    entity: str,
    rows: list[PayrollRegisterRow],
    people: list[dict],
    actor: Actor,
) -> JournalEntry | None:
    if not rows:
        return None
    by_number = {item["employee_number"]: item for item in people if item["entity_code"] == entity}
    buckets: dict = {}
    for row in rows:
        person = by_number[row.employee_number]
        department = row.department
        _add(buckets, "5000", department, Decimal(person["gross_wages"]), ZERO)
        _add(buckets, "5100", department, Decimal(person["employer_tax"]), ZERO)
        _add(buckets, "5110", department, Decimal(person["employer_contribution"]), ZERO)
        _add(buckets, "2100", None, ZERO, Decimal(person["net_pay"]))
        _add(buckets, "2200", None, ZERO, Decimal(person["employee_withholdings"]))
        if Decimal(person["employer_tax"]) > 0:
            _add(buckets, "2210", None, ZERO, Decimal(person["employer_tax"]))
        _add(buckets, "2220", None, ZERO, Decimal(person["employer_contribution"]))
    return _post_draft(session, entity, period, f"{entity} wages for {month_of(period).label}", buckets, actor)


def _suspense_journal(
    session: Session,
    period: str,
    entity: str,
    ghost: PayrollRegisterRow,
    funding: list[FundingInstruction],
    actor: Actor,
) -> JournalEntry | None:
    cash = sum(
        (row.amount for row in funding if row.employee_number == ghost.employee_number and row.cleared),
        ZERO,
    )
    if cash <= 0:
        return None
    buckets = {}
    _add(buckets, "1210", ghost.department, cash, ZERO)
    _add(buckets, "1000", None, ZERO, cash)
    return _post_draft(
        session,
        entity,
        period,
        f"{entity} suspense for unconfirmed person {ghost.employee_number}",
        buckets,
        actor,
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
) -> JournalEntry | None:
    if entity != "UK":
        return None
    month = month_of(period)
    buckets: dict = {}
    rates = ENTITY_RATES["UK"]
    policy = load_policy()
    for row in rows:
        days = accrual_days(row.pay_period_end, month.end)
        if days <= 0:
            continue
        hr = _hr_for(hr_rows, row.employee_number)
        monthly = (hr.annual_salary / 12) if hr is not None else Decimal(row.gross_wages)
        wages = accrue_wages(monthly, days, month_length(month.end))
        tax = quantize(wages * rates["employer_tax"])
        contrib = policy_contribution(wages, policy.contribution_rate("UK"))
        department = row.department
        _add(buckets, "5000", department, wages, ZERO)
        _add(buckets, "5100", department, tax, ZERO)
        _add(buckets, "5110", department, contrib, ZERO)
        _add(buckets, "2150", None, ZERO, wages)
        _add(buckets, "2160", None, ZERO, tax + contrib)
    return _post_draft(session, entity, period, f"UK month-end accrual for {month.label}", buckets, actor)


def _uk_accrual_reversal(session: Session, period: str, actor: Actor) -> JournalEntry | None:
    prior = previous_period(period)
    prior_pay = list(session.scalars(select(PayrollRegisterRow).where(PayrollRegisterRow.period == prior)))
    if not prior_pay:
        return None
    uk = [row for row in prior_pay if row.entity_code == "UK"]
    hr_rows = list(session.scalars(select(HrCensusRow).where(HrCensusRow.period == prior)))
    if not uk:
        return None
    month = month_of(prior)
    buckets: dict = {}
    rates = ENTITY_RATES["UK"]
    policy = load_policy()
    for row in uk:
        days = accrual_days(row.pay_period_end, month.end)
        if days <= 0:
            continue
        hr = _hr_for(hr_rows, row.employee_number)
        monthly = (hr.annual_salary / 12) if hr is not None else Decimal(row.gross_wages)
        wages = accrue_wages(monthly, days, month_length(month.end))
        tax = quantize(wages * rates["employer_tax"])
        contrib = policy_contribution(wages, policy.contribution_rate("UK"))
        department = row.department
        _add(buckets, "2150", None, wages, ZERO)
        _add(buckets, "2160", None, tax + contrib, ZERO)
        _add(buckets, "5000", department, ZERO, wages)
        _add(buckets, "5100", department, ZERO, tax)
        _add(buckets, "5110", department, ZERO, contrib)
    return _post_draft(
        session,
        "UK",
        period,
        f"UK accrual reversal from {month.label}",
        buckets,
        actor,
    )


def _overpayment_journal(
    session: Session,
    period: str,
    hr_rows: list[HrCensusRow],
    actor: Actor,
    notes: list[dict],
) -> JournalEntry | None:
    leavers = [row for row in hr_rows if row.status == "terminated" and row.termination_date]
    history = list(session.scalars(select(PayrollRegisterRow)))
    buckets: dict = {}
    entity = None
    for person in leavers:
        paid = [
            row
            for row in history
            if row.employee_number == person.employee_number and row.pay_period_end > person.termination_date
        ]
        for row in paid:
            extra = overpaid_gross(row.gross_wages, row.pay_period_start, row.pay_period_end, person.termination_date)
            if extra <= 0:
                continue
            entity = person.entity_code
            _add(buckets, "1200", person.department, extra, ZERO)
            _add(buckets, "5000", person.department, ZERO, extra)
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
    if entity is None:
        return None
    return _post_draft(
        session,
        entity,
        period,
        f"Overpayment receivable for days after HR termination, booked in {month_of(period).label}",
        buckets,
        actor,
    )
