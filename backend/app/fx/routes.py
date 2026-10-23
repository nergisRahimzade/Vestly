"""Exchange rates as people see them: the rate a month closes at, and a manual daily update."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import CLOCK_ADVANCE, LEDGER_READ, require
from app.db import get_db
from app.fx.models import FxRate
from app.fx.store import day_rate, month_end, month_reference

router = APIRouter(prefix="/fx")


@router.get("/rates")
def month_rates(period: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """The reference rates a month translates and closes at: the end-of-day rate of its last day, if stored."""
    require(who, LEDGER_READ, "read exchange rates")
    reference = month_reference(db, period)
    latest = db.scalar(select(FxRate).order_by(FxRate.rate_date.desc(), FxRate.provider_timestamp.desc()).limit(1))
    return {
        "period": period,
        "month_end": month_end(period),
        "final_rate_stored": day_rate(db, month_end(period)) is not None,
        "reference": reference,
        "latest_stored": None if latest is None else {"date": latest.rate_date, "final": latest.final, "source": latest.source},
    }


@router.post("/update")
def update(db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """Run the daily update now (the controller, who also moves the clock)."""
    from app.fx.update import update_daily

    require(who, CLOCK_ADVANCE, "update exchange rates")
    return update_daily(db)
