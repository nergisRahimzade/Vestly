"""The simulated clock. Waiting days for an answer takes one controller click, and nothing runs on wall time."""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.auth.permissions import CLOCK_ADVANCE, require
from app.controls.models import SimClock
from app.ledger.errors import LedgerError
from app.policy.loader import load_policy
from app.record.evidence import record


def today(session: Session) -> str:
    row = session.get(SimClock, 1)
    return row.today if row is not None else load_policy().clock_start


def add_days(iso_date: str, days: int) -> str:
    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()


def days_between(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def advance(session: Session, actor: Actor, days: int) -> dict:
    """Move the clock forward, then send the reminders and escalations that fell due."""
    require(actor, CLOCK_ADVANCE, "advance the clock")
    if not 1 <= days <= 60:
        raise LedgerError("invalid_days", "Advance the clock by 1 to 60 days.", 422)
    before = today(session)
    after = add_days(before, days)
    row = session.get(SimClock, 1)
    if row is None:
        session.add(SimClock(id=1, today=after))
    else:
        row.today = after
    session.flush()
    record(session, actor.handle, "clock_advanced", "clock", "sim", payload={"from": before, "to": after}, sim_at=after)
    from app.controls.approvals import tick

    changes = tick(session)
    session.commit()
    return {"from": before, "today": after, "changes": changes}
