"""Vestly's memory of the company: what happened to each employee, and what is normal in each department.

Built from the loaded months (January 2022 on in the company world), never from the generator:
HR rows month by month, the registers, the payment files and the bank, the approved
compensation actions, the signed grants and the subledger, earlier cases and rulings.

An employee's timeline answers "what has been true of this person, and since when". A
department's baseline is learned from at least four years of history and stored as an
Observation, so a later decision can cite the pattern and the months it rests on.
"""

import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.models import Case
from app.controls.models import Callback
from app.errors import doing
from app.ledger.money import CENT, money_str
from app.memory.models import Observation
from app.policy.loader import load_policy
from app.record.evidence import record
from app.record.models import Precedent
from app.world.models import BankLine, FundingInstruction, HrCensusRow, PayrollRegisterRow, SourceDocument

BASELINE_MONTHS = 48


def comp_actions(session: Session) -> list[dict]:
    """Every approved compensation action on file, with the document it came from."""
    actions = []
    for document in session.scalars(select(SourceDocument).where(SourceDocument.system == "comp_action")):
        with doing("reading compensation actions", document=document.title):
            actions += [{**row, "document": document.content_hash} for row in json.loads(document.body)]
    return actions


def _spans(rows: list, value) -> list[dict]:
    """Collapse month-by-month values into spans: [{"value", "from", "to"}]."""
    spans: list[dict] = []
    for row in rows:
        current = value(row)
        if spans and spans[-1]["value"] == current:
            spans[-1]["to"] = row.period
        else:
            spans.append({"value": current, "from": row.period, "to": row.period})
    return spans


def employee_timeline(session: Session, employee_number: str, through: str) -> dict | None:
    """Everything the loaded history shows about one person up to a month. None if no source names them."""
    with doing("building an employee timeline", employee=employee_number, through=through):
        hr = list(
            session.scalars(
                select(HrCensusRow)
                .where(HrCensusRow.employee_number == employee_number, HrCensusRow.period <= through)
                .order_by(HrCensusRow.period)
            )
        )
        # One row per month (a duplicated HR row is mess, not history).
        by_month = {row.period: row for row in hr}
        hr = [by_month[period] for period in sorted(by_month)]
        pay = list(
            session.scalars(
                select(PayrollRegisterRow)
                .where(PayrollRegisterRow.employee_number == employee_number, PayrollRegisterRow.period <= through)
                .order_by(PayrollRegisterRow.period)
            )
        )
        if not hr and not pay:
            return None
        payments = list(
            session.scalars(
                select(FundingInstruction)
                .where(FundingInstruction.employee_number == employee_number, FundingInstruction.period <= through)
                .order_by(FundingInstruction.period, FundingInstruction.payment_id)
            )
        )
        cleared = {row.payment_id for row in session.scalars(select(BankLine).where(BankLine.payment_id.in_([row.payment_id for row in payments])))}
        latest = hr[-1] if hr else None
        actions = [row for row in comp_actions(session) if row["employee_number"] == employee_number]
        callbacks = session.scalars(select(Callback).where(Callback.employee_number == employee_number).order_by(Callback.called_on))
        cases = session.scalars(select(Case).where(Case.subject_number == employee_number).order_by(Case.period, Case.kind))
        months = [row.period for row in hr]
        return {
            "employee_number": employee_number,
            "legal_name": (latest or pay[-1]).legal_name,
            "in_hr": bool(hr),
            "hire_date": latest.hire_date if latest else None,
            "status": latest.status if latest else "not in HR",
            "termination_date": latest.termination_date if latest else None,
            "months_in_history": len(months),
            "history_from": months[0] if months else None,
            "history_to": months[-1] if months else None,
            "years_of_history": round(len(months) / 12, 1),
            "entity_and_department": _spans(hr, lambda row: f"{row.entity_code} {row.department}"),
            "hr_monthly_salary": _spans(hr, lambda row: money_str((row.annual_salary / 12).quantize(CENT))),
            "bank_accounts": _spans(hr, lambda row: row.bank_account),
            "approved_pay_changes": [
                {key: row[key] for key in ("effective", "entered", "entered_by", "approved_by", "previous_monthly_rate", "monthly_rate")}
                for row in sorted(actions, key=lambda item: item["effective"])
            ],
            "pay_received": [
                {
                    "period": row.period,
                    "entity_code": row.entity_code,
                    "department": row.department,
                    "gross": money_str(row.gross_wages),
                    "net": money_str(row.net_pay),
                    "currency": row.currency,
                    "account": row.destination_account,
                    "pay_period": [row.pay_period_start, row.pay_period_end],
                }
                for row in pay
            ],
            "payments": [
                {"payment_id": row.payment_id, "amount": money_str(row.amount), "account": row.destination_account, "cleared": row.payment_id in cleared}
                for row in payments
            ],
            "call_backs": [{"account": row.account, "outcome": row.outcome, "called_on": row.called_on} for row in callbacks],
            "earlier_cases": [{"key": row.key, "state": row.state, "outcome": row.outcome} for row in cases],
            "rulings": [
                {"id": row.id, "rule": row.rule, "ruling": row.ruling, "decided_by": row.decided_by, "valid_from": row.valid_from}
                for row in session.scalars(select(Precedent).order_by(Precedent.valid_from))
                if json.loads(row.scope_json).get("employee") in (None, employee_number)
                and json.loads(row.scope_json).get("entity") in (None, latest.entity_code if latest else None)
            ],
        }


def department_baseline(session: Session, entity: str, department: str, through: str, months: int = BASELINE_MONTHS) -> dict:
    """Totals only: headcount, pay band and contribution rate per month, over the last `months` months up to `through`."""
    with doing("learning a department baseline", entity=entity, department=department, through=through):
        periods = sorted(
            set(session.scalars(select(PayrollRegisterRow.period).where(PayrollRegisterRow.period <= through)))
        )[-months:]
        rows = list(
            session.scalars(
                select(PayrollRegisterRow).where(
                    PayrollRegisterRow.entity_code == entity,
                    PayrollRegisterRow.department == department,
                    PayrollRegisterRow.period.in_(periods),
                )
            )
        )
        min_group = load_policy().min_group_size
        per_month: dict[str, list[PayrollRegisterRow]] = defaultdict(list)
        for row in rows:
            per_month[row.period].append(row)
        full_months = {period: items for period, items in per_month.items() if len(items) >= min_group}
        gross = [row.gross_wages for items in full_months.values() for row in items if row.retro_gross is None]
        rates = [row.employer_contribution / row.gross_wages for items in full_months.values() for row in items if row.gross_wages]
        return {
            "entity_code": entity,
            "department": department,
            "months": sorted(full_months),
            "months_hidden_small_group": sorted(set(per_month) - set(full_months)),
            "headcount_by_month": {period: len(items) for period, items in sorted(full_months.items())},
            "monthly_gross_band": None if not gross else {
                "low": money_str(min(gross)),
                "median": money_str(Decimal(statistics.median(gross)).quantize(CENT)),
                "high": money_str(max(gross)),
            },
            "employer_contribution_rate": None if not rates else str(Decimal(statistics.median(rates)).quantize(Decimal("0.0001"))),
        }


def learn(session: Session, through: str, actor: str = "vestly.payroll") -> list[Observation]:
    """Learn every department's baseline from the history and record it as an observation (self-learning)."""
    learned = []
    departments = sorted(set(session.execute(select(PayrollRegisterRow.entity_code, PayrollRegisterRow.department).distinct()).all()))
    for entity, department in departments:
        baseline = department_baseline(session, entity, department, through)
        if not baseline["months"]:
            continue
        value = {key: baseline[key] for key in ("monthly_gross_band", "employer_contribution_rate", "headcount_by_month")}
        digest = hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()
        subject = f"{entity}:{department}"
        existing = session.scalar(
            select(Observation).where(Observation.kind == "department_baseline", Observation.subject == subject, Observation.as_of == through, Observation.value_sha256 == digest)
        )
        if existing is not None:
            learned.append(existing)
            continue
        event = record(
            session,
            actor,
            "pattern_learned",
            "observation",
            subject,
            payload={"kind": "department_baseline", "as_of": through, "value": value, "months": baseline["months"]},
        )
        row = Observation(
            kind="department_baseline",
            subject=subject,
            as_of=through,
            value_json=json.dumps(value, sort_keys=True),
            months_json=json.dumps(baseline["months"]),
            value_sha256=digest,
            evidence_id=event.id,
            learned_at=datetime.now(timezone.utc),
        )
        session.add(row)
        session.flush()
        learned.append(row)
    session.commit()
    return learned


def observation_dict(row: Observation) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "subject": row.subject,
        "as_of": row.as_of,
        "value": json.loads(row.value_json),
        "months": json.loads(row.months_json),
        "evidence_id": row.evidence_id,
    }
