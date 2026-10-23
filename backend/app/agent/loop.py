"""The agent loop for one case: model turn, tool calls through the gate, results back, until the model reports.

Code decides everything that matters: the tools run through execute (validated, gated,
recorded), the case outcome stays whatever the case service decided from recorded results,
and the final message is shown only if the claim checker passes it. A refusal, a model
error, max_tokens or the round limit hands the case to a person, never to "done".
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import tools as _tools  # noqa: F401  (registers the catalog)
from app.agent.claims import check_final_message, run_calls, status_report
from app.agent.execute import AgentContext, execute
from app.agent.prompts import SYSTEM_PROMPT, case_brief
from app.agent.registry import tools_for
from app.auth.identities import SEATS, Actor
from app.cases import service as cases
from app.cases.models import Case
from app.ledger.models import new_id
from app.record.evidence import record

MAX_ROUNDS = 25


@dataclass
class AgentRun:
    run_id: str
    case_id: str
    status: str  # reported, blocked, needs_human
    shown: str
    problems: list[str] = field(default_factory=list)
    tool_statuses: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "status": self.status,
            "shown": self.shown,
            "problems": self.problems,
            "tools": self.tool_statuses,
            "claims": self.claims,
        }


def run_case(session: Session, case: Case, model, max_rounds: int = MAX_ROUNDS) -> AgentRun:
    seat = Actor(case.seat, "agent")
    run_id = new_id()
    record(session, seat.handle, "agent_run_started", "agent_run", run_id, case_id=case.id, payload={"model": model.name, "case": case.key})
    session.commit()
    ctx = AgentContext(session=session, case=case, seat=seat, run_id=run_id)
    tools = [spec.schema() for spec in tools_for(SEATS[seat.handle])]
    messages: list[dict] = [{"role": "user", "content": case_brief(case)}]
    final_text: str | None = None
    stopped: str | None = None
    for _round in range(max_rounds):
        try:
            turn = model.turn(SYSTEM_PROMPT, tools, messages)
        except Exception as exc:  # the model call itself failed: name it, hand the case to a person
            stopped = f"The model call failed: {type(exc).__name__}: {exc}"
            break
        if turn.stop_reason == "refusal":
            stopped = "The model declined the request."
            break
        if turn.stop_reason == "max_tokens":
            stopped = "The model ran out of room for its answer (max_tokens)."
            break
        messages.append({"role": "assistant", "content": turn.raw_content})
        calls = [block for block in turn.content if block["type"] == "tool_use"]
        if not calls:
            final_text = "".join(block["text"] for block in turn.content if block["type"] == "text")
            break
        # Every result of this turn goes back in one user message.
        results = [execute(ctx, block["name"], block["input"]) for block in calls]
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": block["id"], "content": result.to_json(), "is_error": result.status in ("failed", "refused")}
                    for block, result in zip(calls, results)
                ],
            }
        )
    else:
        stopped = f"The round limit of {max_rounds} was reached before the case was reported."

    session.refresh(case)
    statuses = [{"tool": event_tool(row), "status": row.reason_code, "evidence_id": row.id} for row in run_calls(session, run_id)]
    if stopped is not None:
        _hand_to_person(session, case, seat.handle, stopped)
        shown = status_report(run_calls(session, run_id), f"Vestly stopped and handed the case to a person. {stopped}")
        run = AgentRun(run_id, case.id, "needs_human", shown, [stopped], statuses)
    else:
        check = check_final_message(session, run_id, final_text or "", seat.handle, case.id)
        claims = [] if check.report is None else [claim.model_dump() for claim in check.report.claims]
        run = AgentRun(run_id, case.id, "reported" if check.allowed else "blocked", check.shown, check.problems, statuses, claims)
    record(
        session,
        seat.handle,
        "agent_run_finished",
        "agent_run",
        run_id,
        case_id=case.id,
        reason_code=run.status,
        payload={"status": run.status, "shown": run.shown[:4000], "problems": run.problems, "claims": run.claims},
    )
    session.commit()
    return run


def run_open_cases(session: Session, period: str, model) -> list[AgentRun]:
    """Work every case of the month that is not finished, one agent run each."""
    rows = session.scalars(
        select(Case).where(Case.period == period, Case.state.not_in(cases.FINAL)).order_by(Case.kind, Case.subject_number)
    )
    return [run_case(session, case, model) for case in list(rows)]


def event_tool(row) -> str:
    import json

    return json.loads(row.payload_json)["tool"]


def _hand_to_person(session: Session, case: Case, actor: str, reason: str) -> None:
    if case.state in cases.FINAL or case.state == "needs_human":
        return
    if "needs_human" not in cases.TRANSITIONS[case.state]:
        cases.move(session, case, "investigating", actor, "Re-checking before handing the case to a person.")
    cases.move(session, case, "needs_human", actor, reason, "needs_human")
    session.commit()
