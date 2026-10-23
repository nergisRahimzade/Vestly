"""Who may see one person's pay, and the record of every time someone did."""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.auth.models import AccessLog
from app.auth.permissions import SOURCES_AGGREGATE, SOURCES_INDIVIDUAL, allowed
from app.ledger.errors import LedgerError


def open_case_subjects(_session: Session) -> frozenset[str]:
    """Employee numbers with an open exception case. There is no case store yet, so none."""
    return frozenset()


def individual_scope(session: Session, who: Actor, employee_number: str | None) -> str | None:
    """Return the employee filter this caller must use, or refuse.

    Readers with individual access may list everyone. Vestly may read one person,
    and only while a case on that person is open.
    """
    if allowed(who, SOURCES_INDIVIDUAL):
        return employee_number
    if allowed(who, SOURCES_AGGREGATE):
        if employee_number is not None and employee_number in open_case_subjects(session):
            return employee_number
        raise LedgerError(
            "forbidden",
            "Vestly sees one person's pay only inside an open case on that person. "
            "Department totals are at /world/payroll/totals.",
            403,
        )
    raise LedgerError("forbidden", "Payroll and equity sources are not visible to this identity.", 403)


def log_access(session: Session, who: Actor, resource: str, period: str | None, subjects: list[str]) -> None:
    session.add(
        AccessLog(
            at=datetime.now(timezone.utc),
            handle=who.handle,
            role=who.role,
            resource=resource,
            period=period,
            subjects=json.dumps(sorted(set(subjects))),
        )
    )
    session.commit()
