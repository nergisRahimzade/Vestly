from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.db import get_db
from app.ledger.money import money_str
from app.ledger.routes import journal_out
from app.payroll.service import draft_payroll

router = APIRouter(prefix="/payroll")


@router.post("/drafts")
def create_payroll_drafts(
    period: str,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> dict:
    result = draft_payroll(db, period, who)
    return {
        "period": result["period"],
        "policy_version": result["policy_version"],
        "policy_hash": result["policy_hash"],
        "people": result["people"],
        "notes": result["notes"],
        "already_on_file": result["already_on_file"],
        "journals": [
            {
                **journal_out(entry).model_dump(),
                "balanced": entry_balanced(entry),
            }
            for entry in result["journals"]
        ],
    }


def entry_balanced(entry) -> bool:
    debit = sum((line.debit for line in entry.lines), Decimal("0"))
    credit = sum((line.credit for line in entry.lines), Decimal("0"))
    return debit == credit > 0 and money_str(debit) == money_str(credit)
