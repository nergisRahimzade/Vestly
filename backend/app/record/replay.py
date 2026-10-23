"""As-of replay: what Vestly knew on a date, rebuilt from the record. A filtered read, never a re-run.

Drops count once they had arrived (received on or before the date), documents by their hash,
precedents if they were in force that day, decisions and answers if they were recorded by then
(on the simulated clock), and the policy version the last of those decisions ran under.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cases.models import Case, CaseTransition
from app.errors import doing
from app.ledger.errors import LedgerError
from app.policy.loader import load_policy
from app.record.evidence import as_dict as event_dict
from app.record.models import EvidenceEvent
from app.record.precedents import as_dict as precedent_dict
from app.record.precedents import search_precedents
from app.world.models import DropLoad, HrCensusRow, SourceDocument


def replay(session: Session, on: str, *, employee: str | None = None, case_id: str | None = None) -> dict:
    with doing("replaying the record", on=on, employee=employee, case=case_id):
        from datetime import date

        try:
            date.fromisoformat(on)
        except ValueError:
            raise LedgerError("invalid_date", "Replay a date as YYYY-MM-DD.", 422) from None
        drops = list(session.scalars(select(DropLoad).where(DropLoad.received_on <= on).order_by(DropLoad.period)))
        periods = [row.period for row in drops]
        documents = list(session.scalars(select(SourceDocument).where(SourceDocument.received_on <= on)))
        state: dict = {
            "as_of": on,
            "drops_known": periods,
            "latest_month_known": periods[-1] if periods else None,
            "documents_known": len(documents),
        }
        events = [
            row
            for row in session.scalars(select(EvidenceEvent).where(EvidenceEvent.sim_at.is_not(None), EvidenceEvent.sim_at <= on).order_by(EvidenceEvent.seq))
        ]
        state["policy_hash"] = events[-1].policy_hash if events else load_policy().content_hash
        entity = None
        if employee is not None:
            hr = session.scalar(
                select(HrCensusRow)
                .where(HrCensusRow.employee_number == employee, HrCensusRow.period.in_(periods or [""]))
                .order_by(HrCensusRow.period.desc())
                .limit(1)
            )
            entity = hr.entity_code if hr else None
            state["employee"] = None if hr is None else {
                "employee_number": hr.employee_number,
                "legal_name": hr.legal_name,
                "as_of_month": hr.period,
                "status": hr.status,
                "entity_code": hr.entity_code,
                "department": hr.department,
                "termination_date": hr.termination_date,
                "termination_recorded_on": hr.termination_recorded_on,
                "bank_account": hr.bank_account,
            }
        state["precedents_in_force"] = [precedent_dict(row) for row in search_precedents(session, on, employee=employee, entity=entity)]
        if case_id is not None:
            case = session.get(Case, case_id)
            if case is None:
                raise LedgerError("unknown_case", f"No case {case_id}.", 404)
            steps = session.scalars(select(CaseTransition).where(CaseTransition.case_id == case.id, CaseTransition.on <= on).order_by(CaseTransition.seq))
            state["case"] = {
                "key": case.key,
                "opened_on": case.opened_on,
                "steps": [{"seq": row.seq, "to": row.to_state, "outcome": row.outcome, "by": row.actor, "reason": row.reason, "on": row.on, "evidence": row.evidence_id} for row in steps],
            }
            state["case_events"] = [event_dict(row) for row in events if row.case_id == case.id]
        return state

