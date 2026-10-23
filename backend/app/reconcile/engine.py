import json
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.ledger.errors import LedgerError
from app.ledger.money import money_str
from app.ledger.service import parse_period
from app.policy.loader import Policy, load_policy
from app.reconcile.models import Reconciliation
from app.world.drops import fixture_dir
from app.world.loader import WORLD_VERSION
from app.world.models import EquitySnapshot, HrCensusRow, LedgerExportLine, PayrollRegisterRow, WorldLoad


def reconcile_period(session: Session, period: str) -> list[Reconciliation]:
    parse_period(period)
    if session.get(WorldLoad, WORLD_VERSION) is None:
        raise LedgerError("world_not_loaded", "Load the company file drops before reconciling.", 409)
    drop_path = fixture_dir() / f"{period}.json"
    if not drop_path.exists():
        raise LedgerError("period_not_loaded", f"No file drop is loaded for {period}.", 404)
    policy = load_policy()
    findings = _employment_findings(session, period, policy) + [_fx_finding(period, policy)]
    session.execute(delete(Reconciliation).where(Reconciliation.period == period))
    session.add_all(findings)
    session.commit()
    return findings


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
        ledger_line = _stock_expense_line(session, period, hr.entity_code, hr.department)
        peer_count = len(
            list(
                session.scalars(
                    select(EquitySnapshot).where(
                        EquitySnapshot.period == period,
                        EquitySnapshot.entity_code == hr.entity_code,
                        EquitySnapshot.department == hr.department,
                    )
                )
            )
        )
        latest = max(payroll_rows, key=lambda row: row.pay_period_end) if payroll_rows else None
        payroll_disagrees = latest is not None and latest.pay_period_end > hr.termination_date
        equity_disagrees = equity is not None and equity.platform_status == "vesting" and equity.monthly_expense > 0
        ledger_disagrees = ledger_line is not None and ledger_line.debit > 0
        if not (payroll_disagrees or equity_disagrees or ledger_disagrees):
            continue
        claims = {
            "hr": (
                f"status {hr.status}, termination_date {hr.termination_date}, "
                f"recorded {hr.termination_recorded_on}."
            ),
            "payroll": _payroll_claim(latest),
            "equity": _equity_claim(equity),
            "ledger": _ledger_claim(hr.legal_name, hr.entity_code, hr.department, ledger_line, peer_count),
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


def _fx_finding(period: str, policy: Policy) -> Reconciliation:
    fact = policy.fact("gbp_usd_translation")
    drop = json.loads((fixture_dir() / f"{period}.json").read_text(encoding="utf-8"))
    card = drop["rate_card"]
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


def _equity_claim(equity: EquitySnapshot | None) -> str:
    if equity is None:
        return "No equity snapshot for this person in the period."
    return (
        f"Grant {equity.grant_number} platform_status {equity.platform_status}, "
        f"monthly_expense {money_str(equity.monthly_expense)} {equity.currency}."
    )


def _ledger_claim(
    name: str,
    entity_code: str,
    department: str,
    line: LedgerExportLine | None,
    peer_count: int,
) -> str:
    if line is None or line.debit == 0:
        return f"No stock-compensation debit for {entity_code} {department} in this period."
    only = ""
    if peer_count == 1:
        only = f" {name} is the only {entity_code} {department} grant."
    return (
        f"Account 5200 debit {money_str(line.debit)} {line.currency} for {department} "
        f"on '{line.entry_memo}'.{only}"
    )


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
