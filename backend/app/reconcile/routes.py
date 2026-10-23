import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import RECONCILE_READ, RECONCILE_RUN, require
from app.db import get_db
from app.ledger.errors import LedgerError
from app.ledger.money import money_str
from app.policy.loader import load_policy
from app.reconcile.engine import finding_dict, latest_run, reconcile_period
from app.reconcile.models import Reconciliation, ReconciliationRun

router = APIRouter()


def runner(who: Actor = Depends(current_actor)) -> Actor:
    require(who, RECONCILE_RUN, "run a reconciliation")
    return who


def reader(who: Actor = Depends(current_actor)) -> Actor:
    require(who, RECONCILE_READ, "read reconciliations")
    return who


@router.get("/policy")
def policy() -> dict:
    loaded = load_policy()
    return {
        "version": loaded.version,
        "company": loaded.company,
        "content_hash": loaded.content_hash,
        "materiality_absolute": money_str(loaded.materiality_absolute),
        "materiality_currency": loaded.materiality_currency,
        "fx_band": money_str(loaded.fx_band),
        "contributions": {code: money_str(rate) for code, rate in loaded.contributions},
        "approval_limit": money_str(loaded.approval_limit),
        "approval_limit_currency": loaded.approval_limit_currency,
        "callback_window_days": loaded.callback_window_days,
        "roles": {name: list(actions) for name, actions in loaded.roles},
        "rules": {name: text for name, text in loaded.rules},
        "facts": [
            {
                "fact": fact.fact,
                "authority": fact.authority,
                "email_is_source": fact.email_is_source,
                "ranking": [{"source": source.source, "reason": source.reason} for source in fact.ranking],
            }
            for fact in loaded.facts
        ],
    }


def _run(row: ReconciliationRun) -> dict:
    return {
        "id": row.id,
        "period": row.period,
        "sequence": row.sequence,
        "ran_by": row.ran_by,
        "ran_at": row.ran_at.isoformat(),
        "policy_version": row.policy_version,
        "policy_hash": row.policy_hash,
        "inputs_hash": row.inputs_hash,
        "input_documents": json.loads(row.input_documents),
    }


def _run_with_findings(db: Session, run: ReconciliationRun) -> dict:
    rows = db.scalars(
        select(Reconciliation).where(Reconciliation.run_id == run.id).order_by(Reconciliation.subject_name)
    )
    return {"run": _run(run), "findings": [finding_dict(row) for row in rows]}


@router.post("/reconciliations")
def run_reconciliation(
    period: str,
    db: Session = Depends(get_db),
    who: Actor = Depends(runner),
) -> dict:
    run, _ = reconcile_period(db, period, who)
    return _run_with_findings(db, run)


@router.get("/reconciliations")
def get_reconciliation(
    period: str,
    run_id: str | None = None,
    db: Session = Depends(get_db),
    _who: Actor = Depends(reader),
) -> dict:
    """The latest run for the period, or an earlier run by id."""
    if run_id is None:
        run = latest_run(db, period)
    else:
        run = db.scalar(
            select(ReconciliationRun).where(ReconciliationRun.id == run_id, ReconciliationRun.period == period)
        )
    if run is None:
        raise LedgerError("no_reconciliation", f"No reconciliation run for {period}.", 404)
    return _run_with_findings(db, run)


@router.get("/reconciliations/runs")
def list_runs(period: str, db: Session = Depends(get_db), _who: Actor = Depends(reader)) -> list[dict]:
    rows = db.scalars(
        select(ReconciliationRun).where(ReconciliationRun.period == period).order_by(ReconciliationRun.sequence)
    )
    return [_run(row) for row in rows]
