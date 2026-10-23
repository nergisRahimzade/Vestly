from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ledger.errors import LedgerError
from app.ledger.money import money_str
from app.ledger.routes import actor
from app.ledger.service import Actor, require_controller
from app.world.loader import WORLD_VERSION, load_world
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
READ_ROLES = {"controller", "vestly_agent"}


def reader(who: Actor = Depends(actor)) -> Actor:
    if who.role not in READ_ROLES:
        raise LedgerError(
            "forbidden",
            "Payroll and equity sources are visible to the controller and to Vestly.",
            403,
        )
    return who


def _money(value) -> str | None:
    if value is None:
        return None
    return money_str(value)


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
def load(db: Session = Depends(get_db), who: Actor = Depends(actor)) -> dict:
    require_controller(who, "load the company file drops")
    return load_world(db)


@router.get("/people")
def people(db: Session = Depends(get_db), _who: Actor = Depends(reader)) -> list[dict]:
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


@router.get("/hr")
def hr(period: str, db: Session = Depends(get_db), _who: Actor = Depends(reader)) -> list[dict]:
    rows = db.scalars(select(HrCensusRow).where(HrCensusRow.period == period).order_by(HrCensusRow.legal_name))
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
def payroll(period: str, db: Session = Depends(get_db), _who: Actor = Depends(reader)) -> list[dict]:
    rows = db.scalars(
        select(PayrollRegisterRow).where(PayrollRegisterRow.period == period).order_by(PayrollRegisterRow.legal_name)
    )
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
def funding(period: str, db: Session = Depends(get_db), _who: Actor = Depends(reader)) -> list[dict]:
    rows = db.scalars(
        select(FundingInstruction).where(FundingInstruction.period == period).order_by(FundingInstruction.payment_id)
    )
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
def equity(period: str, db: Session = Depends(get_db), _who: Actor = Depends(reader)) -> list[dict]:
    rows = db.scalars(select(EquitySnapshot).where(EquitySnapshot.period == period).order_by(EquitySnapshot.grant_number))
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
def bank(period: str, db: Session = Depends(get_db), _who: Actor = Depends(reader)) -> list[dict]:
    rows = db.scalars(select(BankLine).where(BankLine.period == period).order_by(BankLine.payment_id))
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
def documents(period: str, db: Session = Depends(get_db), _who: Actor = Depends(reader)) -> list[dict]:
    rows = db.scalars(select(SourceDocument).where(SourceDocument.period == period).order_by(SourceDocument.title))
    return [
        {
            "system": row.system,
            "period": row.period,
            "title": row.title,
            "content_hash": row.content_hash,
            "received_on": row.received_on,
            "body": row.body,
        }
        for row in rows
    ]
