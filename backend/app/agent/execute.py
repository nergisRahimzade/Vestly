"""Run one tool call: validate, gate, run, record, and hand back an honest ToolResult.

Nothing here retries. A crash or a timeout becomes a failed result naming the error, so the
model has to report it. Every call writes one evidence row (reads included) under the agent
run, and the result carries that row's id.
"""

import json
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.registry import REGISTRY
from app.agent.results import ToolResult
from app.auth.identities import SEATS, Actor
from app.cases.models import Case
from app.controls.gate import check_role, log_refusal
from app.ledger.errors import LedgerError
from app.record.evidence import record
from app.record.models import EvidenceEvent


@dataclass
class AgentContext:
    """One agent run on one case, under one seat."""

    session: Session
    case: Case
    seat: Actor
    run_id: str
    results: list[ToolResult] = field(default_factory=list)

    @property
    def seat_name(self) -> str:
        return SEATS[self.seat.handle]

    @property
    def period(self) -> str:
        return self.case.period

    def run_events(self, tool: str | None = None) -> list[EvidenceEvent]:
        """This run's tool-call rows, oldest first."""
        rows = self.session.scalars(
            select(EvidenceEvent)
            .where(EvidenceEvent.subject_type == "agent_run", EvidenceEvent.subject_id == self.run_id, EvidenceEvent.action == "tool_called")
            .order_by(EvidenceEvent.seq)
        )
        return [row for row in rows if tool is None or json.loads(row.payload_json)["tool"] == tool]


def execute(ctx: AgentContext, name: str, raw_input: dict) -> ToolResult:
    result = _run(ctx, name, raw_input)
    if not isinstance(result, ToolResult):
        result = ToolResult.failed(f"{name} returned {type(result).__name__}, not a ToolResult.")
    event = record(
        ctx.session,
        ctx.seat.handle,
        "tool_called",
        "agent_run",
        ctx.run_id,
        case_id=ctx.case.id,
        reason_code=result.status,
        payload={"tool": name, "args": raw_input, **{key: value for key, value in result.as_dict().items() if key != "evidence_id"}},
    )
    ctx.session.commit()
    result = result.with_evidence(event.id)
    ctx.results.append(result)
    return result


def _run(ctx: AgentContext, name: str, raw_input: dict) -> ToolResult:
    spec = REGISTRY.get(name)
    if spec is None:
        return ToolResult.failed(f"unknown_tool: there is no tool named {name}.")
    if ctx.seat_name not in spec.seats:
        return ToolResult.refused("ACC-1", f"{name} is not a tool of the {ctx.seat_name} seat.")
    try:
        args = spec.input_model.model_validate(raw_input)
    except ValidationError as error:
        return ToolResult.failed(f"invalid_input: {error.errors()[0]['msg']} at {error.errors()[0]['loc']}")
    verdict = check_role(ctx.seat, spec.action)
    if not verdict.allowed:
        return ToolResult.refused(verdict.rule, verdict.reason)
    try:
        return spec.run(ctx, args)
    except LedgerError as exc:
        if exc.rule:
            log_refusal(exc, ctx.session)
            return ToolResult.refused(exc.rule, exc.message)
        ctx.session.rollback()
        return ToolResult.failed(f"{exc.code}: {exc.message}")
    except Exception as exc:  # a crash is a failed result the model must report; never retried here
        from app.errors import describe, record_error, where

        ctx.session.rollback()
        error_id = record_error(exc, request=f"tool {name} on case {getattr(ctx.case, 'key', None)}", actor=ctx.seat.handle, session=ctx.session)
        return ToolResult.failed(
            describe(exc),
            {"tool": name, "case": getattr(ctx.case, "key", None), "where": where(exc), "error_id": error_id},
        )
