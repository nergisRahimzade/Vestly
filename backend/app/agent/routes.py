"""Run the agent on a case, and read back what one run did."""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.loop import run_case
from app.agent.model import default_model
from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import AUDIT_READ, require
from app.cases.models import Case
from app.db import get_db
from app.ledger.errors import LedgerError
from app.record.evidence import as_dict
from app.record.models import EvidenceEvent

router = APIRouter(prefix="/agent")


class AskIn(BaseModel):
    question: str = Field(max_length=2000)


@router.post("/ask")
def ask_vestly(body: AskIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """Ask Vestly a question. One person's pay only for those allowed to see it (PRV-1); totals otherwise."""
    from app.agent.ask import ask

    return ask(db, who, body.question)


@router.post("/cases/{case_id}/run")
def run(case_id: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """The case's own seat, or the controller, starts a run. Vestly works under the case's seat identity."""
    case = db.get(Case, case_id)
    if case is None:
        raise LedgerError("unknown_case", f"No case {case_id}.", 404)
    if who.handle != case.seat and who.role != "controller":
        raise LedgerError("forbidden", f"Only {case.seat} or the controller can run this case.", 403, rule="ACC-1", actor=who.handle, action="run_agent")
    return run_case(db, case, default_model()).as_dict()


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    require(who, AUDIT_READ, "read an agent run")
    rows = list(
        db.scalars(
            select(EvidenceEvent).where(EvidenceEvent.subject_type == "agent_run", EvidenceEvent.subject_id == run_id).order_by(EvidenceEvent.seq)
        )
    )
    if not rows:
        raise LedgerError("unknown_run", f"No agent run {run_id}.", 404)
    finished = next((json.loads(row.payload_json) for row in rows if row.action == "agent_run_finished"), None)
    return {"run_id": run_id, "finished": finished, "events": [as_dict(row) for row in rows]}
