import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ledger.errors import LedgerError
from app.ledger.money import money_str
from app.ledger.routes import actor
from app.ledger.service import Actor
from app.policy.loader import load_policy
from app.reconcile.engine import reconcile_period
from app.reconcile.models import Reconciliation

router = APIRouter()
READ_ROLES = {"controller", "vestly_agent"}


def reader(who: Actor = Depends(actor)) -> Actor:
    if who.role not in READ_ROLES:
        raise LedgerError("forbidden", "Reconciliations are visible to the controller and to Vestly.", 403)
    return who


def _finding(row: Reconciliation) -> dict:
    return {
        "period": row.period,
        "fact": row.fact,
        "subject_number": row.subject_number,
        "subject_name": row.subject_name,
        "disposition": row.disposition,
        "authority": row.authority,
        "trusted_value": row.trusted_value,
        "explanation": row.explanation,
        "ranking": json.loads(row.ranking_json),
        "policy_version": row.policy_version,
        "policy_hash": row.policy_hash,
    }


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


@router.post("/reconciliations")
def run_reconciliation(
    period: str,
    db: Session = Depends(get_db),
    _who: Actor = Depends(reader),
) -> list[dict]:
    return [_finding(row) for row in reconcile_period(db, period)]


@router.get("/reconciliations")
def list_reconciliations(
    period: str,
    db: Session = Depends(get_db),
    _who: Actor = Depends(reader),
) -> list[dict]:
    rows = db.scalars(
        select(Reconciliation).where(Reconciliation.period == period).order_by(Reconciliation.subject_name)
    )
    return [_finding(row) for row in rows]
