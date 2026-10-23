"""The one shape every agent tool returns (decision D17). No tool returns a free-text success message.

status says what happened: ok, failed, pending (waiting on a person) or refused (the policy
gate said no, with the rule id in error). partial and fallback say when only part came back or
a substitute was used. evidence_id is the audit-log row the call wrote.

establishes lists the outcomes this result can back, as (subject, outcome) pairs, worked out
by code from the result itself. The claim checker accepts a claim only if a result in the
same run establishes it, so a tool cannot be talked into "approved" by its own wording.
"""

import json
from dataclasses import dataclass, field, replace

STATUSES = ("ok", "failed", "pending", "refused")
# The outcomes a claim may state. Anything else in a claim is refused by the checker.
OUTCOMES = (
    "found",
    "not_found",
    "held",
    "ready",
    "rejected_duplicate",
    "needs_human",
    "asked",
    "drafted",
    "pending_approval",
    "approved",
    "posted",
    "rejected",
    "requested",
    "released",
    "paid",
    "converted",
    "reconciled",
    "not_reconciled",
    "tests_passed",
    "tests_failed",
    "refused",
    "failed",
    "closed",
)


@dataclass(frozen=True)
class ToolResult:
    status: str
    data: dict = field(default_factory=dict)
    error: str | None = None
    evidence_id: str | None = None
    partial: bool = False
    fallback: dict | None = None
    establishes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"Unknown tool status {self.status!r}.")
        for _subject, outcome in self.establishes:
            if outcome not in OUTCOMES:
                raise ValueError(f"Unknown outcome {outcome!r}.")
        if self.status in ("failed", "refused") and not self.error:
            raise ValueError("A failed or refused result must say why.")

    @classmethod
    def ok(cls, data: dict, *, establishes=(), partial: bool = False, fallback: dict | None = None) -> "ToolResult":
        return cls("ok", data, None, None, partial, fallback, tuple(establishes))

    @classmethod
    def pending(cls, data: dict, *, establishes=()) -> "ToolResult":
        return cls("pending", data, None, None, False, None, tuple(establishes))

    @classmethod
    def failed(cls, error: str, data: dict | None = None, *, establishes=()) -> "ToolResult":
        return cls("failed", data or {}, error, None, False, None, tuple(establishes))

    @classmethod
    def refused(cls, rule: str | None, reason: str) -> "ToolResult":
        return cls("refused", {"rule": rule}, f"{rule}: {reason}" if rule else reason)

    def with_evidence(self, evidence_id: str) -> "ToolResult":
        return replace(self, evidence_id=evidence_id)

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "data": self.data,
            "error": self.error,
            "evidence_id": self.evidence_id,
            "partial": self.partial,
            "fallback": self.fallback,
            "establishes": [{"subject": subject, "outcome": outcome} for subject, outcome in self.establishes],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, default=str)
