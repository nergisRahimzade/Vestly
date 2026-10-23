"""The month-end close as one job: check every source, draft, open cases, and ask for the money still owed.

Server code (decision D5). Each piece of work is done under the seat it belongs to: the payroll
seat reconciles, drafts payroll and asks for funding; the equity seat drafts the stock-expense
true-up. Running the close again finds the same cases instead of opening new ones.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identities import EQUITY_SEAT, PAYROLL_SEAT, Actor
from app.cases.models import Case, CaseLink
from app.cases.service import FINAL, as_dict, link_drafts, sync_cases
from app.controls.gate import log_refusal
from app.controls.models import FundingRequest
from app.controls.posting import request_funding
from app.equity.service import draft_equity
from app.errors import doing
from app.ledger.errors import LedgerError
from app.payroll.service import draft_contribution_catchups, draft_payroll
from app.record.evidence import record
from app.reconcile.engine import reconcile_period

PAYROLL = Actor(PAYROLL_SEAT, "agent")
EQUITY = Actor(EQUITY_SEAT, "agent")
FUNDING_KINDS = {"uncleared_funding"}


def run_close(session: Session, period: str) -> dict:
    with doing("reconciling the sources", period=period):
        run, _ = reconcile_period(session, period, PAYROLL)
    with doing("learning what is normal from the history", period=period):
        from app.memory.history import learn

        learned = learn(session, period, PAYROLL.handle)
    with doing("drafting payroll", period=period):
        payroll = draft_payroll(session, period, PAYROLL)
    waiting: list[dict] = []
    try:
        with doing("drafting the equity true-up", period=period):
            equity = draft_equity(session, period, EQUITY)
    except LedgerError as exc:
        if exc.code != "missing_reference_rate":
            raise
        # D14 and D20: nothing is translated at another day's rate. The true-up waits; the rest of the close goes on.
        session.rollback()
        equity = {"journals": []}
        waiting.append({"step": "equity_trueup", "reason": exc.message})
        record(session, EQUITY.handle, "close_step_waiting", "period", period, reason_code="missing_reference_rate", payload={"step": "equity_trueup", "reason": exc.message})
        session.commit()
    with doing("opening and refreshing cases", period=period):
        cases = sync_cases(session, run.id, period)
    with doing("drafting the corrections the history cases call for", period=period):
        corrections = draft_contribution_catchups(session, period, PAYROLL)
    with doing("linking drafts to their cases", period=period):
        link_drafts(session, period)
    with doing("asking for the funding still owed", period=period):
        funding, refused = request_due_funding(session, period)
    return {
        "period": period,
        "reconciliation_run": run.id,
        "journals": [entry.number for entry in payroll["journals"] + equity["journals"] + corrections["journals"]],
        "held": payroll["held"],
        "cases": [as_dict(case) for case in cases],
        "funding_requests": [row.id for row in funding],
        "refused": refused,
        "waiting": waiting,
        "observations": len(learned),
    }


def request_due_funding(session: Session, period: str) -> tuple[list[FundingRequest], list[dict]]:
    """One request per entity for the ready payment lines no live request carries yet."""
    due: dict[str, list[Case]] = defaultdict(list)
    for case in session.scalars(
        select(Case).where(
            Case.period == period, Case.kind.in_(FUNDING_KINDS), Case.outcome == "ready", Case.state.not_in(FINAL)
        )
    ):
        if not _has_live_request(session, case):
            due[case.entity_code].append(case)
    made, refused = [], []
    for entity, cases in sorted(due.items()):
        payments = [case.subject_number for case in cases]
        try:
            made.append(
                request_funding(
                    session,
                    PAYROLL,
                    period,
                    payments,
                    f"Re-send the {entity} payments the bank has not cleared: {', '.join(payments)}.",
                    [case.id for case in cases],
                )
            )
        except LedgerError as exc:
            log_refusal(exc, session)
            refused.append({"entity": entity, "payments": payments, "rule": exc.rule, "reason": exc.message})
    return made, refused


def _has_live_request(session: Session, case: Case) -> bool:
    ids = session.scalars(select(CaseLink.resource_id).where(CaseLink.case_id == case.id, CaseLink.resource == "funding_request"))
    return any(session.get(FundingRequest, item).status not in ("declined", "voided") for item in ids)
