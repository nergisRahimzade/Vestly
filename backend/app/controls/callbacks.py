"""BNK-1: a bank-detail change counts only after HR confirms it by phone, on a number already on file."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.auth.permissions import CALLBACK_RECORD, require
from app.controls.clock import add_days, today
from app.controls.gate import ALLOWED, Verdict, deny
from app.controls.models import Callback
from app.ledger.errors import LedgerError
from app.policy.loader import load_policy
from app.record.evidence import record
from app.world.models import HrCensusRow

OUTCOMES = ("confirmed", "denied")


def record_callback(
    session: Session,
    actor: Actor,
    employee_number: str,
    account: str,
    outcome: str,
    called_on: str | None = None,
    note: str | None = None,
) -> Callback:
    require(actor, CALLBACK_RECORD, "record a call-back")
    if outcome not in OUTCOMES:
        raise LedgerError("invalid_outcome", "A call-back is confirmed or denied.", 422)
    row = Callback(
        employee_number=employee_number,
        account=account,
        outcome=outcome,
        called_by=actor.handle,
        called_on=called_on or today(session),
        note=note,
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    record(
        session,
        actor.handle,
        "callback_recorded",
        "employee",
        employee_number,
        payload={"account": account, "outcome": outcome, "called_on": row.called_on, "note": note},
        sim_at=today(session),
    )
    session.commit()
    return row


def hr_bank_account(session: Session, employee_number: str, period: str) -> str | None:
    """The HR bank master for a person as of a month: the latest HR row up to that month."""
    row = session.scalar(
        select(HrCensusRow)
        .where(HrCensusRow.employee_number == employee_number, HrCensusRow.period <= period)
        .order_by(HrCensusRow.period.desc())
        .limit(1)
    )
    return None if row is None else row.bank_account


def _other_holders(session: Session, employee_number: str, account: str, period: str) -> list[str]:
    """Anyone else set up with this account in any loaded HR month up to the period."""
    rows = session.execute(
        select(HrCensusRow.employee_number, HrCensusRow.legal_name)
        .where(HrCensusRow.bank_account == account, HrCensusRow.employee_number != employee_number, HrCensusRow.period <= period)
        .distinct()
    ).all()
    return sorted(f"{name} ({number})" for number, name in rows)


def check_pay_destination(
    session: Session, employee_number: str, account: str, period: str, pay_date: str, checked_on: str | None = None
) -> Verdict:
    """Net pay may go to the HR bank master. Any other account needs a confirmed call-back in the window.

    An account the controller blocked (a BNK-1 precedent scoped to it) takes no pay at all, for anyone, from the
    day of the ruling. Money is judged by the rulings in force on the day it moves (`checked_on`, today by default),
    whatever its pay date.
    """
    from app.record.precedents import search_precedents

    blocked = search_precedents(session, checked_on or today(session), rule="BNK-1", account=account)
    if blocked:
        ruling = blocked[0]
        return deny("BNK-1", f"Account {account} is blocked by {ruling.id} ({ruling.decided_by}, {ruling.decided_on}): {ruling.ruling}")
    master = hr_bank_account(session, employee_number, period)
    others = _other_holders(session, employee_number, account, period)
    if master is not None and master == account and not others:
        return ALLOWED
    window = load_policy().callback_window_days
    earliest = add_days(pay_date, -window)
    calls = list(
        session.scalars(
            select(Callback)
            .where(
                Callback.employee_number == employee_number,
                Callback.account == account,
                Callback.called_on >= earliest,
                Callback.called_on <= pay_date,
            )
            .order_by(Callback.called_on.desc(), Callback.recorded_at.desc())
        )
    )
    if not calls:
        if master == account and others:
            return deny(
                "BNK-1",
                f"Account {account} is {employee_number}'s HR bank master, but it also belonged to {', '.join(others)}; "
                f"no call-back between {earliest} and {pay_date} confirms it.",
            )
        return deny(
            "BNK-1",
            f"Account {account} is not the HR bank master for {employee_number}, and no call-back between "
            f"{earliest} and {pay_date} confirms it.",
        )
    if calls[0].outcome != "confirmed":
        return deny("BNK-1", f"HR's call-back on {calls[0].called_on} says {employee_number} did not ask for {account}.")
    return ALLOWED
