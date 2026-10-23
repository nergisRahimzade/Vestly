"""Vestly's memory as people see it: one employee's history (individual access, logged) and a department's normal."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import SOURCES_AGGREGATE, require
from app.db import get_db
from app.ledger.errors import LedgerError
from app.memory.history import department_baseline, employee_timeline, observation_dict
from app.memory.models import Observation
from app.world.access import individual_scope, log_access
from app.world.models import PayrollRegisterRow

router = APIRouter(prefix="/memory")

DEPARTMENTS = ("Engineering", "Product", "GTM", "G&A")


def _through(db: Session, through: str | None) -> str:
    """The month to read up to: the one asked for, or the latest loaded."""
    latest = db.scalar(select(func.max(PayrollRegisterRow.period)))
    if latest is None:
        raise LedgerError("no_history", "No month is loaded yet.", 404)
    return through or latest


@router.get("/employees/{employee_number}")
def employee_history(
    employee_number: str, through: str | None = None, db: Session = Depends(get_db), who: Actor = Depends(current_actor)
) -> dict:
    """One person's timeline. Their pay is visible only to individual readers (PRV-1), and every read is logged."""
    individual_scope(db, who, employee_number)
    month = _through(db, through)
    timeline = employee_timeline(db, employee_number, month)
    if timeline is None:
        raise LedgerError("unknown_employee", f"No source names {employee_number} up to {month}.", 404)
    log_access(db, who, "employee_history", month, [employee_number])
    return timeline


@router.get("/departments/{entity_code}/{department}")
def department_history(
    entity_code: str, department: str, through: str | None = None, db: Session = Depends(get_db), who: Actor = Depends(current_actor)
) -> dict:
    """Totals only; months with fewer than three people are hidden."""
    require(who, SOURCES_AGGREGATE, "read department history")
    if entity_code not in ("US", "UK", "DE") or department not in DEPARTMENTS:
        raise LedgerError("invalid_department", f"No department {entity_code} {department}.", 404)
    month = _through(db, through)
    learned = db.scalar(
        select(Observation)
        .where(Observation.kind == "department_baseline", Observation.subject == f"{entity_code}:{department}")
        .order_by(Observation.as_of.desc(), Observation.learned_at.desc())
        .limit(1)
    )
    return {**department_baseline(db, entity_code, department, month), "observation": None if learned is None else observation_dict(learned)}
