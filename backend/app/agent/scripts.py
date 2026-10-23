"""Scripts for the scripted model: how Vestly works a case when no live model is attached.

The honest script reads the case, the policy and the precedents, runs the checks its kind
needs, and writes a report that states only what the tool results show: the case outcome,
who was asked, any failed or refused call with its error, any fallback with its rate date.
It quotes the finding word for word. Tests and the scorer use it; other scripts in the tests
lie on purpose, to prove the claim checker stops them.
"""

import json

from app.agent.model import tool_results

# The checks each kind of case calls for, beyond reading the case. Each entry: (tool, input builder).
INVESTIGATIONS = {
    "bank_detail_change": [("get_employee_record", "person"), ("search_precedents", "bnk"), ("extract_document", "document")],
    "uncleared_funding": [("reconcile_funding", "entity"), ("get_bank_transactions", "payment")],
    "funding_booked_not_cleared": [("reconcile_funding", "entity"), ("get_bank_transactions", "payment")],
    "contribution_rate_break": [("compute_contributions", "entity")],
    "forfeiture_still_expensed": [("compute_sbc_expense", "none")],
    "missing_grant_approval": [("compute_sbc_expense", "none")],
    "vest_schedule_mismatch": [("compute_sbc_expense", "none")],
    "duplicate_grant": [("compute_sbc_expense", "none")],
    "unknown_platform_grant": [("compute_sbc_expense", "none")],
}
DEFAULT_INVESTIGATION = [("get_employee_record", "person"), ("match_register", "person")]
OUTCOME_SENTENCE = {
    "held": "{subject} is held: nothing moves for it until a person answers.",
    "ready": "{subject} is ready: its entries wait in the approvals inbox for a person.",
    "rejected_duplicate": "{subject} is rejected as a duplicate.",
    "needs_human": "{subject} needs a person to take it over.",
}


def call(name: str, arguments: dict) -> dict:
    return {"type": "tool_use", "name": name, "input": arguments}


def honest_script(messages: list[dict], tools: list[dict]) -> list[dict]:
    available = {tool["name"] for tool in tools}
    rounds = sum(1 for message in messages if message["role"] == "assistant")
    done = tool_results(messages)
    if rounds == 0:
        return [call("get_case", {}), call("get_policy", {}), call("search_precedents", {"rule": None})]
    case = _result(done, "get_case")
    if rounds == 1 and case is not None and case["status"] == "ok":
        checks = _investigation(case["data"], available)
        if checks:
            return checks
    return [{"type": "text", "text": json.dumps(honest_report(done))}]


def _investigation(case: dict, available: set[str]) -> list[dict]:
    inputs = {
        "person": {"employee_number": case["subject_number"]},
        "payment": {"payment_id": case["subject_number"]},
        "entity": {"entity_code": case["entity_code"]},
        "bnk": {"rule": "BNK-1"},
        "none": {},
    }
    emails = [item for item in case["finding"]["basis"] if item.get("type") == "document" and "email" in (item.get("title") or "").lower()]
    if emails:
        inputs["document"] = {"content_hash": emails[0]["doc_sha256"]}
    plan = INVESTIGATIONS.get(case["kind"], DEFAULT_INVESTIGATION)
    return [call(name, inputs[builder]) for name, builder in plan if name in available and builder in inputs]


def honest_report(done: list[dict]) -> dict:
    """A report built only from tool results: what they establish, quoted where it reads the record."""
    sentences: list[str] = []
    claims: list[dict] = []
    case_call = _call(done, "get_case")
    if case_call is not None and case_call["result"]["status"] == "ok":
        case, evidence = case_call["result"]["data"], case_call["result"]["evidence_id"]
        subject = case["subject_number"]
        sentences.append(f"{case['subject_name']} ({subject}), case {case['key']}.")
        sentences.append(f'The finding reads: "{case["finding"]["explanation"]}"')
        if case["outcome"] in OUTCOME_SENTENCE:
            sentences.append(OUTCOME_SENTENCE[case["outcome"]].format(subject=subject))
            claims.append({"outcome": case["outcome"], "subject": subject, "evidence_id": evidence})
        owners = sorted({question["asked_to"] for question in case["questions"] if question["status"] == "open"})
        if owners:
            sentences.append(f"Waiting for {', '.join(owners)} to answer.")
            claims += [{"outcome": "asked", "subject": owner, "evidence_id": evidence} for owner in owners]
    for item in done:
        result = item["result"]
        if result["status"] in ("failed", "refused"):
            sentences.append(f'{item["name"]} returned {result["status"]}: "{result["error"]}"')
        if result.get("fallback"):
            fallback = result["fallback"]
            sentences.append(
                f"{item['name']} used a fallback rate {fallback['rate']} dated {fallback['rate_date']} because {fallback['reason']}."
            )
        if result.get("partial"):
            sentences.append(f"{item['name']} returned only part of what was asked.")
    if not sentences:
        sentences.append("No tool returned a usable result in this run.")
    return {"summary": " ".join(sentences), "claims": claims}


def _call(done: list[dict], name: str) -> dict | None:
    return next((item for item in done if item["name"] == name), None)


def _result(done: list[dict], name: str) -> dict | None:
    found = _call(done, name)
    return None if found is None else found["result"]
