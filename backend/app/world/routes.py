from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import SOURCES_AGGREGATE, SOURCES_INDIVIDUAL, WORLD_LOAD, allowed, require
from app.db import get_db
from app.ledger.money import money_str
from app.policy.loader import load_policy
from app.world.access import individual_scope, log_access
from app.world.loader import WORLD_VERSION, load_drop, load_world
from app.world.models import (
    BankLine,
    Department,
    EquitySnapshot,
    FundingInstruction,
    HrCensusRow,
    PayrollRegisterRow,
    Person,
    SourceDocument,
    WorldLoad,
)

router = APIRouter(prefix="/world")


def aggregate_reader(who: Actor = Depends(current_actor)) -> Actor:
    require(who, SOURCES_AGGREGATE, "read payroll and equity sources")
    return who


def _money(value) -> str | None:
    if value is None:
        return None
    return money_str(value)


def _totals(rows: list, amount_fields: tuple[str, ...], min_group_size: int, by_department: bool = True) -> list[dict]:
    """Sum rows per entity and department, plus one row per entity.

    A group covering fewer than min_group_size people shows its headcount and no amounts,
    so a total never reveals one person's pay.
    """
    groups: dict[tuple[str, str | None], list] = defaultdict(list)
    for row in rows:
        groups[(row.entity_code, None)].append(row)
        if by_department:
            groups[(row.entity_code, row.department)].append(row)
    result = []
    for (entity_code, department), members in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        suppressed = len(members) < min_group_size
        amounts = None
        if not suppressed:
            amounts = {
                field: money_str(sum((getattr(member, field) for member in members), Decimal("0.00")))
                for field in amount_fields
            }
        result.append(
            {
                "entity_code": entity_code,
                "department": department,
                "currency": members[0].currency,
                "headcount": len(members),
                "suppressed": suppressed,
                "amounts": amounts,
            }
        )
    return result


@router.get("/departments")
def departments(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Department).order_by(Department.name))
    return [{"name": row.name} for row in rows]


@router.get("/summary")
def summary(db: Session = Depends(get_db)) -> dict:
    loaded = db.get(WorldLoad, WORLD_VERSION) is not None
    people = int(db.scalar(select(func.count()).select_from(Person)) or 0)
    documents = int(db.scalar(select(func.count()).select_from(SourceDocument)) or 0)
    return {"company": "Northlight Labs", "loaded": loaded, "people": people, "documents": documents}


@router.post("/load")
def load(db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    require(who, WORLD_LOAD, "load the company file drops")
    return load_world(db, who)


@router.post("/drops")
def receive_drop(drop: dict, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """A month's file drop arrives. Loading the same drop again changes nothing."""
    return load_drop(db, drop, who)


@router.get("/people")
def people(db: Session = Depends(get_db), _who: Actor = Depends(aggregate_reader)) -> list[dict]:
    rows = db.scalars(select(Person).order_by(Person.legal_name))
    return [
        {
            "employee_number": row.employee_number,
            "legal_name": row.legal_name,
            "entity_code": row.entity_code,
            "department": row.department,
            "listed_in_hr": row.listed_in_hr,
        }
        for row in rows
    ]


@router.get("/hr/headcount")
def hr_headcount(period: str, db: Session = Depends(get_db), _who: Actor = Depends(aggregate_reader)) -> list[dict]:
    counts: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen: set[tuple[str, str]] = set()
    for row in db.scalars(select(HrCensusRow).where(HrCensusRow.period == period)):
        if (row.employee_number, row.status) in seen:
            continue
        seen.add((row.employee_number, row.status))
        counts[(row.entity_code, row.department)][row.status] += 1
    return [
        {"entity_code": entity_code, "department": department, "by_status": dict(statuses)}
        for (entity_code, department), statuses in sorted(counts.items())
    ]


@router.get("/payroll/totals")
def payroll_totals(period: str, db: Session = Depends(get_db), _who: Actor = Depends(aggregate_reader)) -> list[dict]:
    rows = list(db.scalars(select(PayrollRegisterRow).where(PayrollRegisterRow.period == period)))
    fields = ("gross_wages", "employee_withholdings", "net_pay", "employer_tax", "employer_contribution")
    return _totals(rows, fields, load_policy().min_group_size)


@router.get("/equity/totals")
def equity_totals(period: str, db: Session = Depends(get_db), _who: Actor = Depends(aggregate_reader)) -> list[dict]:
    rows = list(db.scalars(select(EquitySnapshot).where(EquitySnapshot.period == period)))
    return _totals(rows, ("monthly_expense",), load_policy().min_group_size)


@router.get("/funding/totals")
def funding_totals(period: str, db: Session = Depends(get_db), _who: Actor = Depends(aggregate_reader)) -> list[dict]:
    minimum = load_policy().min_group_size
    by_entity: dict[str, list[FundingInstruction]] = defaultdict(list)
    for row in db.scalars(select(FundingInstruction).where(FundingInstruction.period == period)):
        by_entity[row.entity_code].append(row)
    result = []
    for entity_code, rows in sorted(by_entity.items()):
        cleared = [row for row in rows if row.cleared]
        waiting = [row for row in rows if not row.cleared]

        def total(members: list[FundingInstruction]) -> str | None:
            if len(members) < minimum:
                return None
            return money_str(sum((member.amount for member in members), Decimal("0.00")))

        result.append(
            {
                "entity_code": entity_code,
                "currency": rows[0].currency,
                "lines": len(rows),
                "cleared_lines": len(cleared),
                "uncleared_lines": len(waiting),
                "amount": total(rows),
                "cleared_amount": total(cleared),
                "uncleared_amount": total(waiting),
            }
        )
    return result


@router.get("/bank/totals")
def bank_totals(period: str, db: Session = Depends(get_db), _who: Actor = Depends(aggregate_reader)) -> list[dict]:
    rows = list(db.scalars(select(BankLine).where(BankLine.period == period)))
    return _totals(rows, ("amount",), load_policy().min_group_size, by_department=False)


@router.get("/hr")
def hr(
    period: str,
    employee_number: str | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> list[dict]:
    scope = individual_scope(db, who, employee_number)
    query = select(HrCensusRow).where(HrCensusRow.period == period)
    if scope is not None:
        query = query.where(HrCensusRow.employee_number == scope)
    rows = list(db.scalars(query.order_by(HrCensusRow.legal_name)))
    log_access(db, who, "hr", period, [row.employee_number for row in rows])
    return [
        {
            "employee_number": row.employee_number,
            "legal_name": row.legal_name,
            "entity_code": row.entity_code,
            "department": row.department,
            "status": row.status,
            "hire_date": row.hire_date,
            "termination_date": row.termination_date,
            "termination_recorded_on": row.termination_recorded_on,
            "annual_salary": _money(row.annual_salary),
            "currency": row.currency,
            "bank_account": row.bank_account,
            "bank_routing": row.bank_routing,
        }
        for row in rows
    ]


@router.get("/payroll")
def payroll(
    period: str,
    employee_number: str | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> list[dict]:
    scope = individual_scope(db, who, employee_number)
    query = select(PayrollRegisterRow).where(PayrollRegisterRow.period == period)
    if scope is not None:
        query = query.where(PayrollRegisterRow.employee_number == scope)
    rows = list(db.scalars(query.order_by(PayrollRegisterRow.legal_name)))
    log_access(db, who, "payroll", period, [row.employee_number for row in rows])
    return [
        {
            "employee_number": row.employee_number,
            "legal_name": row.legal_name,
            "entity_code": row.entity_code,
            "department": row.department,
            "pay_period_start": row.pay_period_start,
            "pay_period_end": row.pay_period_end,
            "gross_wages": _money(row.gross_wages),
            "employee_withholdings": _money(row.employee_withholdings),
            "net_pay": _money(row.net_pay),
            "employer_tax": _money(row.employer_tax),
            "employer_contribution": _money(row.employer_contribution),
            "currency": row.currency,
            "destination_account": row.destination_account,
            "retro_effective_date": row.retro_effective_date,
            "retro_gross": _money(row.retro_gross),
        }
        for row in rows
    ]


@router.get("/funding")
def funding(
    period: str,
    employee_number: str | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> list[dict]:
    scope = individual_scope(db, who, employee_number)
    query = select(FundingInstruction).where(FundingInstruction.period == period)
    if scope is not None:
        query = query.where(FundingInstruction.employee_number == scope)
    rows = list(db.scalars(query.order_by(FundingInstruction.payment_id)))
    log_access(db, who, "funding", period, [row.employee_number for row in rows])
    return [
        {
            "payment_id": row.payment_id,
            "employee_number": row.employee_number,
            "legal_name": row.legal_name,
            "entity_code": row.entity_code,
            "amount": _money(row.amount),
            "currency": row.currency,
            "destination_account": row.destination_account,
            "kind": row.kind,
            "cleared": row.cleared,
        }
        for row in rows
    ]


@router.get("/equity")
def equity(
    period: str,
    employee_number: str | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> list[dict]:
    scope = individual_scope(db, who, employee_number)
    query = select(EquitySnapshot).where(EquitySnapshot.period == period)
    if scope is not None:
        query = query.where(EquitySnapshot.employee_number == scope)
    rows = list(db.scalars(query.order_by(EquitySnapshot.grant_number)))
    log_access(db, who, "equity", period, [row.employee_number for row in rows])
    return [
        {
            "grant_number": row.grant_number,
            "employee_number": row.employee_number,
            "legal_name": row.legal_name,
            "entity_code": row.entity_code,
            "department": row.department,
            "grant_date": row.grant_date,
            "cliff_end": row.cliff_end,
            "vest_end": row.vest_end,
            "fair_value": _money(row.fair_value),
            "currency": row.currency,
            "board_approved": row.board_approved,
            "board_approval_date": row.board_approval_date,
            "platform_status": row.platform_status,
            "monthly_expense": _money(row.monthly_expense),
        }
        for row in rows
    ]


@router.get("/bank")
def bank(
    period: str,
    employee_number: str | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> list[dict]:
    scope = individual_scope(db, who, employee_number)
    query = select(BankLine).where(BankLine.period == period)
    if scope is not None:
        query = query.where(BankLine.payment_id.contains(scope))
    rows = list(db.scalars(query.order_by(BankLine.payment_id)))
    log_access(db, who, "bank", period, [row.payment_id for row in rows])
    return [
        {
            "entity_code": row.entity_code,
            "posted_on": row.posted_on,
            "amount": _money(row.amount),
            "currency": row.currency,
            "payment_id": row.payment_id,
            "description": row.description,
            "destination_account": row.destination_account,
        }
        for row in rows
    ]


@router.get("/documents")
def documents(period: str, db: Session = Depends(get_db), who: Actor = Depends(aggregate_reader)) -> list[dict]:
    """Document metadata for any source reader. Bodies hold individual pay, so they need individual access."""
    with_body = allowed(who, SOURCES_INDIVIDUAL)
    rows = list(db.scalars(select(SourceDocument).where(SourceDocument.period == period).order_by(SourceDocument.title)))
    if with_body:
        log_access(db, who, "documents", period, [row.title for row in rows])
    return [
        {
            "system": row.system,
            "period": row.period,
            "title": row.title,
            "content_hash": row.content_hash,
            "received_on": row.received_on,
            "body": row.body if with_body else None,
        }
        for row in rows
    ]
