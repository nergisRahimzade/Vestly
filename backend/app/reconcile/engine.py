import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.equity.service import rebuild_schedules
from app.ledger.errors import LedgerError
from app.ledger.money import money_str
from app.ledger.service import parse_period
from app.policy.loader import Policy, load_policy
from app.reconcile.detectors import Finding, detect_all, load_context
from app.reconcile.models import Reconciliation, ReconciliationRun
from app.record.evidence import record
from app.world.loader import rate_card_for, world_loaded
from app.world.models import SourceDocument


def reconcile_period(session: Session, period: str, actor: Actor) -> tuple[ReconciliationRun, list[Reconciliation]]:
    """Run every detector and keep the result as a new run. Earlier runs are left as they were."""
    parse_period(period)
    if not world_loaded(session):
        raise LedgerError("world_not_loaded", "Load the company file drops before reconciling.", 409)
    rate_card = rate_card_for(session, period)
    if rate_card is None:
        raise LedgerError("period_not_loaded", f"No file drop is loaded for {period}.", 404)
    policy = load_policy()
    rebuild_schedules(session, period, actor)
    run = _start_run(session, period, actor, policy)
    findings = [_row(run, finding, policy) for finding in detect_all(load_context(session, period, policy, rate_card))]
    session.add_all(findings)
    record(
        session,
        actor.handle,
        "reconciliation_run",
        "reconciliation_run",
        run.id,
        payload={
            "period": period,
            "sequence": run.sequence,
            "inputs_hash": run.inputs_hash,
            "findings": [
                {
                    "kind": row.kind,
                    "subject": row.subject_number,
                    "disposition": row.disposition,
                    "trusted": row.trusted_value,
                    "ask": json.loads(row.ask_json),
                }
                for row in findings
            ],
        },
    )
    session.commit()
    return run, findings


def latest_run(session: Session, period: str) -> ReconciliationRun | None:
    return session.scalar(
        select(ReconciliationRun)
        .where(ReconciliationRun.period == period)
        .order_by(ReconciliationRun.sequence.desc())
        .limit(1)
    )


def _start_run(session: Session, period: str, actor: Actor, policy: Policy) -> ReconciliationRun:
    # The reconciler reads this month, every earlier month, and the signed grants, so all of them are its inputs.
    documents = list(
        session.scalars(
            select(SourceDocument)
            .where((SourceDocument.period <= period) | (SourceDocument.period == "static"))
            .order_by(SourceDocument.content_hash)
        )
    )
    inputs = [{"title": row.title, "period": row.period, "content_hash": row.content_hash} for row in documents]
    inputs_hash = hashlib.sha256(
        "\n".join(f"{row.period} {row.content_hash}" for row in documents).encode("utf-8")
    ).hexdigest()
    previous = session.scalar(
        select(func.max(ReconciliationRun.sequence)).where(ReconciliationRun.period == period)
    )
    run = ReconciliationRun(
        period=period,
        sequence=int(previous or 0) + 1,
        ran_by=actor.handle,
        ran_at=datetime.now(timezone.utc),
        policy_version=policy.version,
        policy_hash=policy.content_hash,
        inputs_hash=inputs_hash,
        input_documents=json.dumps(inputs),
    )
    session.add(run)
    session.flush()
    return run


def _row(run: ReconciliationRun, finding: Finding, policy: Policy) -> Reconciliation:
    decision = finding.materiality
    return Reconciliation(
        run_id=run.id,
        period=run.period,
        fact=finding.fact,
        kind=finding.kind,
        subject_number=finding.subject_number,
        subject_name=finding.subject_name,
        entity_code=finding.entity_code,
        disposition=finding.disposition,
        authority=finding.authority,
        trusted_value=finding.trusted_value,
        explanation=finding.explanation,
        ranking_json=json.dumps(finding.ranking),
        reason_code=finding.reason_code,
        ask_json=json.dumps(finding.ask),
        amount=finding.amount,
        currency=finding.currency,
        materiality=None if decision is None else decision.outcome,
        materiality_reason=None if decision is None else decision.reason,
        draft_allowed=finding.draft_allowed,
        basis_json=json.dumps(finding.basis),
        details_json=json.dumps(finding.details, default=str),
        policy_version=policy.version,
        policy_hash=policy.content_hash,
    )


def finding_dict(row: Reconciliation) -> dict:
    return {
        "id": row.id,
        "period": row.period,
        "kind": row.kind,
        "fact": row.fact,
        "subject_number": row.subject_number,
        "subject_name": row.subject_name,
        "entity_code": row.entity_code,
        "disposition": row.disposition,
        "authority": row.authority,
        "trusted_value": row.trusted_value,
        "explanation": row.explanation,
        "ranking": json.loads(row.ranking_json),
        "reason_code": row.reason_code,
        "ask": json.loads(row.ask_json),
        "amount": None if row.amount is None else money_str(row.amount),
        "currency": row.currency,
        "materiality": row.materiality,
        "materiality_reason": row.materiality_reason,
        "draft_allowed": row.draft_allowed,
        "basis": json.loads(row.basis_json),
        "details": json.loads(row.details_json),
        "policy_version": row.policy_version,
        "policy_hash": row.policy_hash,
    }
