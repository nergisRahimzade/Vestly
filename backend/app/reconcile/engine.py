import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.equity.models import EquityGrantMonth
from app.equity.service import department_subledger, grant_month, rebuild_schedules
from app.ledger.errors import LedgerError
from app.ledger.money import money_str
from app.ledger.service import parse_period
from app.policy.loader import Policy, load_policy
from app.reconcile.models import Reconciliation, ReconciliationRun
from app.world.loader import rate_card_for, world_loaded
from app.world.models import (
    EquitySnapshot,
    HrCensusRow,
    LedgerExportLine,
    PayrollRegisterRow,
    SourceDocument,
)


def reconcile_period(session: Session, period: str, actor: Actor) -> tuple[ReconciliationRun, list[Reconciliation]]:
    """Run the reconciler and keep the result as a new run. Earlier runs are left as they were."""
    parse_period(period)
    if not world_loaded(session):
        raise LedgerError("world_not_loaded", "Load the company file drops before reconciling.", 409)
    rate_card = rate_card_for(session, period)
    if rate_card is None:
        raise LedgerError("period_not_loaded", f"No file drop is loaded for {period}.", 404)
    policy = load_policy()
    rebuild_schedules(session, period, actor)
    run = _start_run(session, period, actor, policy)
    findings = _employment_findings(session, period, policy) + [_fx_finding(period, policy, rate_card)]
    for finding in findings:
        finding.run_id = run.id
    session.add_all(findings)
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


def _employment_findings(session: Session, period: str, policy: Policy) -> list[Reconciliation]:
    fact = policy.fact("employment_dates")
    hr_rows = session.scalars(
        select(HrCensusRow).where(HrCensusRow.period == period, HrCensusRow.status == "terminated")
    )
    findings: list[Reconciliation] = []
    for hr in hr_rows:
        if hr.termination_date is None:
            continue
        payroll_rows = list(
            session.scalars(
                select(PayrollRegisterRow).where(PayrollRegisterRow.employee_number == hr.employee_number)
            )
        )
        equity = session.scalar(
            select(EquitySnapshot).where(
                EquitySnapshot.period == period,
                EquitySnapshot.employee_number == hr.employee_number,
            )
        )
        subledger = grant_month(session, period, hr.employee_number)
        ledger_line = _stock_expense_line(session, period, hr.entity_code, hr.department)
        department_net = department_subledger(session, period, hr.entity_code, hr.department)
        latest = max(payroll_rows, key=lambda row: row.pay_period_end) if payroll_rows else None
        payroll_disagrees = latest is not None and latest.pay_period_end > hr.termination_date
        equity_disagrees = _equity_disagrees(equity, subledger)
        ledger_disagrees = _ledger_disagrees(ledger_line, department_net)
        if not (payroll_disagrees or equity_disagrees or ledger_disagrees):
            continue
        claims = {
            "hr": (
                f"status {hr.status}, termination_date {hr.termination_date}, "
                f"recorded {hr.termination_recorded_on}."
            ),
            "payroll": _payroll_claim(latest),
            "equity": _equity_claim(equity, subledger),
            "ledger": _ledger_claim(hr.entity_code, hr.department, ledger_line, department_net, subledger),
        }
        ranking = [
            {"rank": index, "source": source.source, "reason": source.reason, "claim": claims[source.source]}
            for index, source in enumerate(fact.ranking, start=1)
        ]
        explanation = (
            f"Employment end date for {hr.legal_name}. "
            f"HR is the authority: {claims['hr']} "
            f"Payroll is downstream: {claims['payroll']} "
            f"Equity platform: {claims['equity']} "
            f"Ledger: {claims['ledger']} "
            f"Trusted value: {hr.termination_date}."
        )
        findings.append(
            _row(
                period=period,
                fact=fact.fact,
                subject_number=hr.employee_number,
                subject_name=hr.legal_name,
                disposition="conflict",
                authority=fact.authority,
                trusted_value=hr.termination_date,
                explanation=explanation,
                ranking=ranking,
                policy=policy,
            )
        )
    return findings


def _fx_finding(period: str, policy: Policy, card: dict) -> Reconciliation:
    fact = policy.fact("gbp_usd_translation")
    reference = Decimal(card["gbp_usd_reference"])
    used = Decimal(card["gbp_usd_reporting"])
    gap = abs(reference - used) / reference
    inside = gap <= policy.fx_band
    claims = {
        "reference_rate": f"{card['gbp_usd_reference']} on the {period} rate card.",
        "management_pack": f"{card['gbp_usd_reporting']} used in the management pack.",
        "ledger": "UK books stay in GBP. This gap does not rewrite a journal.",
    }
    ranking = [
        {"rank": index, "source": source.source, "reason": source.reason, "claim": claims[source.source]}
        for index, source in enumerate(fact.ranking, start=1)
    ]
    disposition = "explained" if inside else "conflict"
    relation = "inside" if inside else "outside"
    explanation = (
        f"GBPUSD translation for {period}. "
        f"The reference rate is {card['gbp_usd_reference']} and the management pack used {card['gbp_usd_reporting']}. "
        f"The difference is {_percent(gap)}% of the reference rate, {relation} the policy band of {_percent(policy.fx_band)}%. "
        f"{'This is an explained difference, not an exception.' if inside else 'This is outside the band and needs a person.'}"
    )
    return _row(
        period=period,
        fact=fact.fact,
        subject_number="GBPUSD",
        subject_name="GBPUSD",
        disposition=disposition,
        authority=fact.authority,
        trusted_value=card["gbp_usd_reference"],
        explanation=explanation,
        ranking=ranking,
        policy=policy,
    )


def _stock_expense_line(session: Session, period: str, entity_code: str, department: str) -> LedgerExportLine | None:
    return session.scalar(
        select(LedgerExportLine).where(
            LedgerExportLine.period == period,
            LedgerExportLine.entity_code == entity_code,
            LedgerExportLine.account_code == "5200",
            LedgerExportLine.department == department,
            LedgerExportLine.kind == "equity",
        )
    )


def _payroll_claim(latest: PayrollRegisterRow | None) -> str:
    if latest is None:
        return "No payroll register row in the loaded months."
    return (
        f"Last pay period ends {latest.pay_period_end}, "
        f"gross {money_str(latest.gross_wages)} {latest.currency}."
    )


def _equity_disagrees(equity: EquitySnapshot | None, subledger: EquityGrantMonth | None) -> bool:
    if equity is not None and equity.platform_status == "vesting" and equity.monthly_expense > 0:
        return True
    if equity is not None and subledger is not None and equity.monthly_expense != subledger.booked_net_expense:
        return True
    return False


def _ledger_disagrees(line: LedgerExportLine | None, department_net: Decimal) -> bool:
    booked = Decimal("0.00") if line is None else line.debit
    return booked != department_net


def _equity_claim(equity: EquitySnapshot | None, subledger: EquityGrantMonth | None) -> str:
    if equity is None:
        platform = "No equity snapshot for this person in the period."
    else:
        platform = (
            f"Grant {equity.grant_number} platform_status {equity.platform_status}, "
            f"monthly_expense {money_str(equity.monthly_expense)} {equity.currency}."
        )
    if subledger is None:
        return platform
    return (
        f"{platform} Subledger {subledger.grant_number} status {subledger.status}, "
        f"net_expense {money_str(subledger.booked_net_expense or subledger.net_expense)} "
        f"{subledger.booking_currency}."
    )


def _ledger_claim(
    entity_code: str,
    department: str,
    line: LedgerExportLine | None,
    department_net: Decimal,
    subledger: EquityGrantMonth | None,
) -> str:
    if line is None or line.debit == 0:
        booked = f"No stock-compensation debit for {entity_code} {department} in this period."
    else:
        booked = (
            f"Account 5200 debit {money_str(line.debit)} {line.currency} for {department} "
            f"on '{line.entry_memo}'."
        )
    grant = ""
    if subledger is not None:
        grant = (
            f" Grant {subledger.grant_number} subledger net "
            f"{money_str(subledger.booked_net_expense or subledger.net_expense)} {subledger.booking_currency}."
        )
    return f"{booked} Subledger {entity_code} {department} totals {money_str(department_net)}.{grant}"


def _percent(rate: Decimal) -> str:
    value = (rate * Decimal(100)).quantize(Decimal("0.01"))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _row(
    period: str,
    fact: str,
    subject_number: str,
    subject_name: str,
    disposition: str,
    authority: str,
    trusted_value: str,
    explanation: str,
    ranking: list[dict],
    policy: Policy,
) -> Reconciliation:
    return Reconciliation(
        period=period,
        fact=fact,
        subject_number=subject_number,
        subject_name=subject_name,
        disposition=disposition,
        authority=authority,
        trusted_value=trusted_value,
        explanation=explanation,
        ranking_json=json.dumps(ranking),
        policy_version=policy.version,
        policy_hash=policy.content_hash,
    )
