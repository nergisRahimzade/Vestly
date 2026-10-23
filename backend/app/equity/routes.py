from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.db import get_db
from app.equity.service import draft_equity, schedules_as_of
from app.ledger.money import money_str
from app.ledger.routes import journal_out

router = APIRouter(prefix="/equity")


@router.get("/schedules")
def get_schedules(
    period: str,
    grant_number: str | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> dict:
    rows = schedules_as_of(db, period, who, grant_number)
    return {"period": period, "schedules": rows}


@router.post("/drafts")
def create_equity_drafts(
    period: str,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> dict:
    result = draft_equity(db, period, who)
    return {
        "period": result["period"],
        "schedule_run": result["schedule_run"],
        "window_start": result["window_start"],
        "explained_from": result["explained_from"],
        "notes": result["notes"],
        "trueup": result["trueup"],
        "already_on_file": result["already_on_file"],
        "schedules": result["schedules"],
        "journals": [
            {**journal_out(entry).model_dump(), "balanced": _balanced(entry)} for entry in result["journals"]
        ],
    }


def _balanced(entry) -> bool:
    debit = sum((line.debit for line in entry.lines), Decimal("0"))
    credit = sum((line.credit for line in entry.lines), Decimal("0"))
    return debit == credit > 0 and money_str(debit) == money_str(credit)
