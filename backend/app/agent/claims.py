"""The claim checker: nothing Vestly says about an outcome reaches a person unless this run's record backs it.

The agent's final message is structured: a summary and a list of claims, each naming an
outcome, a subject and the evidence row (a tool call in this run) that shows it. Before the
message is shown, code checks:

1. every claim cites a tool call from this run whose result establishes exactly that
   (subject, outcome) pair; a result that used a fallback must have its rate date in the text;
2. every outcome word in the summary ("approved", "posted", "paid", "converted", "tests
   pass"...) that is stated, not negated or conditional, sits in a sentence naming the
   subject of a backed claim with that outcome;
3. every quoted passage appears word for word in this run's tool results (a quote attributes
   words to the record; it may not invent them);
4. every number and date in the summary appears in this run's tool calls (faithfulness).

Anything unsupported blocks the message. The block is written to the evidence log as a
false claim, and the person sees a status report built by code from the tool results instead.
"""

import json
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, ValidationError

from app.agent.results import OUTCOMES
from app.record.evidence import record
from app.record.models import EvidenceEvent

# Words that state an outcome, and the outcome they state.
OUTCOME_WORDS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bapproved\b", re.I), "approved"),
    (re.compile(r"\bposted\b", re.I), "posted"),
    (re.compile(r"\b(?:paid|released|transferred|sent|cleared)\b", re.I), "paid"),
    (re.compile(r"\b(?:converted|translated)\b", re.I), "converted"),
    (re.compile(r"\b(?:reconciled|agrees? to the cent)\b", re.I), "reconciled"),
    (re.compile(r"\b(?:all )?tests? (?:all )?pass(?:ed|es)?\b", re.I), "tests_passed"),
    (re.compile(r"\b(?:resolved|completed|done|closed)\b", re.I), "closed"),
    (re.compile(r"\bdrafted\b", re.I), "drafted"),
    (re.compile(r"\brejected as (?:a )?duplicate\b", re.I), "rejected_duplicate"),
)
# A word in the three before an outcome word that makes it not a statement of fact.
NOT_STATED = {
    "not", "never", "no", "pending", "awaiting", "waiting", "until", "unless", "cannot", "without", "once",
    "if", "will", "would", "be", "yet", "before", "can", "could", "should", "may", "nothing", "neither", "nor",
}
QUOTE = re.compile(r"\"([^\"]{3,})\"|“([^”]{3,})”")
NUMBER = re.compile(r"(?<![\w-])\d[\d,]*(?:\.\d+)?(?:-\d{2}(?:-\d{2})?)?(?![\w])")


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str
    subject: str
    evidence_id: str


class FinalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    claims: list[Claim]


def report_schema() -> dict:
    """The JSON schema the model's final message must follow (structured output)."""
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "outcome": {"type": "string", "enum": list(OUTCOMES)},
                        "subject": {"type": "string"},
                        "evidence_id": {"type": "string"},
                    },
                    "required": ["evidence_id", "outcome", "subject"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["claims", "summary"],
        "additionalProperties": False,
    }


@dataclass
class ClaimCheck:
    allowed: bool
    shown: str
    problems: list[str] = field(default_factory=list)
    report: FinalReport | None = None


def run_calls(session, run_id: str) -> list[EvidenceEvent]:
    from sqlalchemy import select

    return list(
        session.scalars(
            select(EvidenceEvent)
            .where(EvidenceEvent.subject_type == "agent_run", EvidenceEvent.subject_id == run_id, EvidenceEvent.action == "tool_called")
            .order_by(EvidenceEvent.seq)
        )
    )


def check_final_message(session, run_id: str, text: str, actor: str, case_id: str | None) -> ClaimCheck:
    calls = run_calls(session, run_id)
    problems = find_problems(calls, text)
    report = None
    if not problems:
        report = FinalReport.model_validate_json(text)
        return ClaimCheck(True, report.summary, [], report)
    record(
        session,
        actor,
        "false_claim",
        "agent_run",
        run_id,
        case_id=case_id,
        reason_code="false_claim",
        payload={"message": text[:4000], "problems": problems},
    )
    session.commit()
    return ClaimCheck(False, status_report(calls, "Vestly's own summary was blocked: it stated something this run's record does not show."), problems)


def find_problems(calls: list[EvidenceEvent], text: str) -> list[str]:
    try:
        report = FinalReport.model_validate_json(text)
    except ValidationError:
        return ["The final message is not the structured report (a summary and claims with evidence ids)."]
    payloads = {row.id: json.loads(row.payload_json) for row in calls}
    problems: list[str] = []
    backed: list[tuple[str, str]] = []
    for claim in report.claims:
        label = f"claim '{claim.outcome}' for {claim.subject} ({claim.evidence_id})"
        if claim.outcome not in OUTCOMES:
            problems.append(f"{label}: '{claim.outcome}' is not an outcome a claim may state.")
            continue
        payload = payloads.get(claim.evidence_id)
        if payload is None:
            problems.append(f"{label}: that evidence row is not a tool call in this run.")
            continue
        established = {(item["subject"], item["outcome"]) for item in payload.get("establishes", [])}
        if (claim.subject, claim.outcome) not in established:
            shown = ", ".join(f"{subject} {outcome}" for subject, outcome in sorted(established)) or "nothing"
            problems.append(f"{label}: {payload['tool']} returned {payload['status']} and establishes {shown}.")
            continue
        fallback = payload.get("fallback")
        if fallback and fallback.get("rate_date") not in report.summary:
            problems.append(f"{label}: {payload['tool']} used a fallback rate dated {fallback['rate_date']}; the summary must say so.")
            continue
        backed.append((claim.subject, claim.outcome))

    record_text = _normalize(" ".join(json.dumps({"args": item.get("args"), "data": item.get("data"), "error": item.get("error")}, default=str, ensure_ascii=False) for item in payloads.values()))
    quoted = [match.group(1) or match.group(2) for match in QUOTE.finditer(report.summary)]
    for passage in quoted:
        if _normalize(passage) not in record_text:
            problems.append(f"The quote \"{passage[:80]}\" does not appear in this run's tool results.")
    unquoted = QUOTE.sub(" ", report.summary)
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", unquoted):
        for pattern, outcome in OUTCOME_WORDS:
            for match in pattern.finditer(sentence):
                if _negated(sentence, match.start()):
                    continue
                if not any(kind == outcome and subject.lower() in sentence.lower() for subject, kind in backed):
                    problems.append(f"Unsupported '{match.group(0)}' in: \"{sentence.strip()[:160]}\"")
    digits_record = record_text.replace(",", "")
    for number in NUMBER.findall(unquoted):
        plain = number.replace(",", "")
        if len(plain) < 3 and "." not in plain:
            continue
        if plain not in digits_record:
            problems.append(f"The figure {number} does not come from any tool result in this run.")
    return problems


def status_report(calls: list[EvidenceEvent], headline: str) -> str:
    """What the tools actually returned, built by code. Shown when the agent's own words cannot be."""
    lines = [headline, "What the tools returned in this run:"]
    for row in calls:
        payload = json.loads(row.payload_json)
        line = f"- {payload['tool']}: {payload['status']} ({row.id})"
        if payload.get("error"):
            line += f", error: {payload['error']}"
        if payload.get("fallback"):
            line += f", fallback rate {payload['fallback']['rate']} dated {payload['fallback']['rate_date']} ({payload['fallback']['reason']})"
        if payload.get("partial"):
            line += ", partial result"
        lines.append(line)
    if not calls:
        lines.append("- no tool was called")
    return "\n".join(lines)


def _negated(sentence: str, position: int) -> bool:
    before = re.findall(r"[a-z']+", sentence[:position].lower())[-3:]
    return any(word in NOT_STATED or word.endswith("n't") for word in before)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\\n", " ").replace('\\"', '"')).strip().lower()
