"""Asking Vestly a question as a person. One person's pay is answered only for those allowed to see it (PRV-1);
anyone else is refused with the rule, and offered the department's totals when the group is big enough."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.auth.permissions import SOURCES_INDIVIDUAL, allowed
from app.controls.clock import today
from app.ledger.money import CENT, money_str
from app.policy.loader import load_policy
from app.record.evidence import record
from app.world.models import HrCensusRow, Person

PAY_WORDS = re.compile(r"\b(salary|salaries|pay|paid|earn|earns|earning|gross|wage|wages|compensation|bonus)\b", re.I)


def ask(session: Session, actor: Actor, question: str) -> dict:
    question = (question or "").strip()
    people = list(session.scalars(select(Person)))
    named = [
        person for person in people
        if person.legal_name.lower() in question.lower() or person.employee_number.lower() in question.lower()
    ]
    about_pay = bool(PAY_WORDS.search(question))
    on = today(session)
    if not named or not about_pay:
        record(session, actor.handle, "question_to_vestly", "question", "general", payload={"question": question, "answered": "not about one person's pay"}, sim_at=on)
        session.commit()
        return {"status": "ok", "answer": "Ask about one person's pay by name, or about a department's totals.", "rule": None}
    person = named[0]
    latest = session.scalar(
        select(HrCensusRow).where(HrCensusRow.employee_number == person.employee_number).order_by(HrCensusRow.period.desc()).limit(1)
    )
    if allowed(actor, SOURCES_INDIVIDUAL) and latest is not None:
        record(session, actor.handle, "individual_read", "employee", person.employee_number, payload={"question": question, "via": "ask"}, sim_at=on)
        session.commit()
        return {
            "status": "ok",
            "rule": None,
            "answer": f"{latest.legal_name}: {money_str((latest.annual_salary / 12).quantize(CENT))} {latest.currency} a month (HR, {latest.period}).",
        }
    offered = _department_totals(session, person.entity_code, person.department, latest.period if latest else None)
    record(
        session,
        actor.handle,
        "refused",
        "action",
        "read one person's pay",
        reason_code="PRV-1",
        payload={"question": question, "about": person.employee_number, "offered": offered},
        sim_at=on,
    )
    session.commit()
    return {
        "status": "refused",
        "rule": "PRV-1",
        "answer": f"PRV-1: {load_policy().rule('PRV-1')} {actor.handle} may not see one person's pay.",
        "offered": offered,
    }


def _department_totals(session: Session, entity: str, department: str, period: str | None) -> dict | None:
    if period is None:
        return None
    rows = list(
        session.scalars(
            select(HrCensusRow).where(
                HrCensusRow.entity_code == entity, HrCensusRow.department == department, HrCensusRow.period == period, HrCensusRow.status == "active"
            )
        )
    )
    unique = {row.employee_number: row for row in rows}
    if len(unique) < load_policy().min_group_size:
        return {"entity_code": entity, "department": department, "period": period, "headcount": len(unique), "total": None, "note": "Group too small to show totals."}
    total = sum(((row.annual_salary / 12).quantize(CENT) for row in unique.values()), CENT * 0)
    return {"entity_code": entity, "department": department, "period": period, "headcount": len(unique), "monthly_salary_total": money_str(total), "currency": rows[0].currency}
