"""The policy gate. Plain code a reviewer can read; the model only ever sees its answer.

Every check returns a Verdict: allowed, or refused with a rule id from the policy file.
A refusal is raised as a LedgerError carrying the rule, and the API's error handler
writes it to the evidence log, so the record shows what was tried and what stopped it.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identities import SEATS, Actor
from app.equity.models import EquityGrantMonth
from app.ledger.errors import LedgerError
from app.ledger.money import CENT
from app.policy.loader import load_policy
from app.record.evidence import record
from app.world.models import PayrollRegisterRow, SourceDocument

# The HTTP status and error code each rule's refusal uses.
REFUSALS = {
    "ACC-1": ("forbidden", 403),
    "SOD-1": ("separation_of_duties", 403),
    "SOD-2": ("separation_of_duties", 403),
    "LIM-1": ("approval_limit", 403),
    "BND-1": ("approval_voided", 409),
    "BNK-1": ("bank_change_unverified", 403),
    "GRT-1": ("missing_grant_approval", 422),
    "CTR-1": ("contribution_error", 422),
    "DUP-1": ("already_booked", 409),
    "CLS-1": ("period_locked", 409),
    "PRV-1": ("forbidden", 403),
}


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    rule: str | None = None
    reason: str = "Allowed."

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "rule": self.rule, "reason": self.reason}


ALLOWED = Verdict(True)


def deny(rule: str, detail: str) -> Verdict:
    return Verdict(False, rule, f"{rule}: {load_policy().rule(rule)} {detail}".strip())


def enforce(verdict: Verdict, actor: Actor, action: str) -> None:
    """Raise the refusal a verdict describes. Does nothing when the verdict allows."""
    if verdict.allowed:
        return
    code, status = REFUSALS[verdict.rule]
    raise LedgerError(code, verdict.reason, status, rule=verdict.rule, actor=actor.handle, action=action)


def log_refusal(exc: LedgerError, session: Session | None = None) -> None:
    """Write a refusal to the evidence log in its own transaction; the refused write was rolled back.

    Callers that work outside the API (the scorer's runs) pass their own session.
    """
    from app.db import SessionLocal

    def write(target: Session) -> None:
        target.rollback()
        record(
            target,
            exc.actor or "unknown",
            "refused",
            "action",
            exc.action or exc.code,
            reason_code=exc.rule,
            payload={"code": exc.code, "message": exc.message},
        )
        target.commit()

    try:
        if session is not None:
            write(session)
            return
        with SessionLocal() as own:
            write(own)
    except Exception:  # the refusal itself must still reach the caller
        pass


# ---------------------------------------------------------------- checks


def check_role(actor: Actor, action: str) -> Verdict:
    """ACC-1: the role's action list in the policy file."""
    if action in load_policy().role_actions(actor.role):
        return ALLOWED
    return deny("ACC-1", f"{actor.handle} ({actor.role}) may not {action.replace('_', ' ')}.")


def check_seat(actor: Actor, seat: str) -> Verdict:
    """Vestly's payroll seat drafts payroll and its equity seat drafts equity, nothing else."""
    if actor.role != "agent" or SEATS.get(actor.handle) == seat:
        return ALLOWED
    return deny("ACC-1", f"{actor.handle} is not the {seat} seat.")


def check_proposal(session: Session, payload: dict) -> Verdict:
    """Checks on a drafted entry's own lines before it exists: GRT-1 and CTR-1."""
    for line in payload.get("lines", []):
        verdict = _grant_check(session, line)
        if not verdict.allowed:
            return verdict
    return _contribution_check(session, payload)


def _grant_check(session: Session, line: dict) -> Verdict:
    """GRT-1: a stock-expense debit may not rest on a grant without board approval."""
    if str(line.get("account_code")) != "5200" or Decimal(str(line.get("debit", "0"))) <= 0:
        return ALLOWED
    for source in line.get("sources") or ():
        if source.get("table") != "equity_grant_months":
            continue
        row = session.get(EquityGrantMonth, source.get("id"))
        if row is not None and not row.board_approved:
            return deny("GRT-1", f"Grant {row.grant_number} has no board approval on file.")
    return ALLOWED


def _contribution_check(session: Session, payload: dict) -> Verdict:
    """CTR-1: an employer pension or social line built from register rows must equal policy rate times gross.

    It judges entries that book the cited pay itself (the same department's wage line equals that
    gross). An accrual books part of a month and is measured on the accrued wages instead.
    """
    policy = load_policy()
    entity = str(payload.get("entity_code", "")).upper()
    wages = {
        line.get("department"): Decimal(str(line.get("debit", "0")))
        for line in payload.get("lines", [])
        if str(line.get("account_code")) == "5000"
    }
    for line in payload.get("lines", []):
        if str(line.get("account_code")) != "5110":
            continue
        rows = [
            session.get(PayrollRegisterRow, source.get("id"))
            for source in line.get("sources") or ()
            if source.get("table") == "payroll_register_rows"
        ]
        rows = [row for row in rows if row is not None]
        if not rows or wages.get(line.get("department")) != sum((row.gross_wages for row in rows), Decimal("0.00")):
            continue
        expected = sum(
            ((row.gross_wages * policy.contribution_rate(entity)).quantize(CENT) for row in rows), Decimal("0.00")
        )
        booked = Decimal(str(line.get("debit", "0"))) - Decimal(str(line.get("credit", "0")))
        if booked != expected:
            return deny(
                "CTR-1",
                f"{line.get('department')} contributions are {booked:.2f}; policy rate on the cited pay gives {expected:.2f}.",
            )
    return ALLOWED


def comp_changes_entered_by(session: Session, employee_numbers: set[str], period: str) -> dict[str, list[str]]:
    """Who keyed a pay change, for people paid in a run, in the run's month: handle -> employee numbers."""

    entered: dict[str, list[str]] = defaultdict(list)
    for document in session.scalars(select(SourceDocument).where(SourceDocument.system == "comp_action")):
        for action in json.loads(document.body):
            if action["employee_number"] in employee_numbers and action["entered"] == period:
                entered[action["entered_by"]].append(action["employee_number"])
    return entered
