import json
from decimal import Decimal
from pathlib import Path

from app.auth.identities import IDENTITIES
from app.world.drops import PEOPLE, SAM_ORTIZ, actions_dir, build_drop, fixture_dir, grants_dir

ANSWER_KEY = Path(__file__).resolve().parents[1] / "fixtures" / "answer_key.json"
REQUIRED = {
    "id", "name", "kind", "period", "subjects", "trusted_source", "ask", "draft_allowed",
    "expected_outcome", "reason_code", "must_cite", "must_not", "related",
}
HANDLES = {handle for handle, _name, _role, _dept in IDENTITIES}


def _key() -> dict:
    return json.loads(ANSWER_KEY.read_text(encoding="utf-8"))


def _roster() -> set[str]:
    numbers = {person["employee_number"] for person in PEOPLE}
    numbers.add(SAM_ORTIZ["employee_number"])
    return numbers


def _balanced(entry: dict) -> bool:
    debits = sum(Decimal(line["debit"]) for line in entry["lines"])
    credits = sum(Decimal(line["credit"]) for line in entry["lines"])
    return debits == credits > 0


def test_answer_key_schema_and_roster():
    key = _key()
    assert key["version"] == "2026-06"
    ids = [row["id"] for row in key["anomalies"]]
    assert ids == [f"A{index:02d}" for index in range(1, 14)]
    roster = _roster()
    names = {person["legal_name"] for person in PEOPLE}
    names.add(SAM_ORTIZ["legal_name"])
    for row in key["anomalies"]:
        assert REQUIRED <= row.keys()
        assert row["period"] in {"2026-03", "2026-06"}
        assert row["ask"] in HANDLES
        assert isinstance(row["draft_allowed"], bool)
        for subject in row["subjects"]:
            assert subject in roster or subject.startswith("PAY-2026-")
    for name in key["true_negatives"]:
        assert name in names or name == "GBPUSD"
    people_in_key = {subject for row in key["anomalies"] for subject in row["subjects"] if not subject.startswith("PAY-")}
    assert people_in_key <= roster


def test_every_case_says_how_it_is_scored():
    """The scoring fields name real outcomes, rules, precedents and authority rows, and well-formed actions."""
    from app.policy.loader import load_policy

    key = _key()
    policy = load_policy()
    rules = {name for name, _text in policy.rules}
    facts = {fact.fact for fact in policy.facts}
    for row in key["anomalies"]:
        assert row["expected_outcome"] in key["outcomes"], row["id"]
        assert row["reason_code"], row["id"]
        cite = row["must_cite"]
        assert set(cite) <= {"rules", "precedents", "authority_row"}
        assert set(cite.get("rules", [])) <= rules, row["id"]
        assert all(item.startswith("P-") for item in cite.get("precedents", []))
        assert cite.get("authority_row") is None or cite["authority_row"] in facts, row["id"]
        for action in row["must_not"]:
            verb = action.split(":", 1)[0]
            assert verb in {"request_funding", "reopen", "request_reopen", "approve_by_agent"}, action
        for subject in row["related"]:
            assert subject.startswith("PAY-2026-06-") and subject not in row["subjects"]
    held = {row["id"] for row in key["anomalies"] if row["expected_outcome"] == "held"}
    assert {row["id"] for row in key["anomalies"] if row["draft_allowed"] is False} <= held


def test_fixture_ledgers_balance_and_match_the_key_cast():
    key = _key()
    june = json.loads((fixture_dir() / "2026-06.json").read_text(encoding="utf-8"))
    march = json.loads((fixture_dir() / "2026-03.json").read_text(encoding="utf-8"))
    for drop in (march, june):
        assert all(_balanced(entry) for entry in drop["ledger_entries"])
        assert drop == build_drop(drop["period"])
    names = {row["legal_name"] for row in june["hr"]} | {row["legal_name"] for row in june["payroll"]}
    for label in ("Nadia Rossi", "Tomas Fischer", "Ines Duarte", "Sam Ortiz"):
        assert label in names
    subjects = {subject for row in key["anomalies"] for subject in row["subjects"]}
    june_numbers = {row["employee_number"] for row in june["hr"]} | {row["employee_number"] for row in june["payroll"]}
    june_payments = {row["payment_id"] for row in june["funding"]}
    for subject in subjects:
        if subject.startswith("PAY-2026-"):
            assert subject in june_payments
        else:
            assert subject in june_numbers or subject in {row["employee_number"] for row in march["payroll"]}


def test_signed_grants_and_comp_actions_exist(client):
    from helpers import CONTROLLER

    grants = {path.stem for path in grants_dir().glob("*.txt")}
    expected = {person["grant"]["number"] for person in PEOPLE if "grant" in person}
    assert grants == expected
    jonah = (grants_dir() / "GR-US-002.txt").read_text(encoding="utf-8")
    assert "Vest end: 2028-03-01" in jonah
    lukas = (grants_dir() / "GR-DE-001.txt").read_text(encoding="utf-8")
    assert "Board approved: no" in lukas
    actions = json.loads((actions_dir() / "2026.json").read_text(encoding="utf-8"))
    evan = next(row for row in actions if row["legal_name"] == "Evan Brooks")
    assert evan["entered_by"] == "sam.kerr"
    assert evan["effective"] == "2026-04-01"
    assert evan["approved_by"] == "lee.park"
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    docs = client.get("/world/documents?period=static", headers=CONTROLLER).json()
    titles = {row["title"] for row in docs}
    assert "GR-US-002" in titles
    assert any(row["system"] == "comp_action" for row in docs)
