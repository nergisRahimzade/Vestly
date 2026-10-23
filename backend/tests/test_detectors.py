"""Each detector fires on its planted case and stays silent on the clean people and the harmless mess.

The expectations come from the answer key, so the key and the detectors cannot drift apart quietly.
"""

import json
from pathlib import Path

import pytest

from app.reconcile.detectors import normalize_key
from app.world.drops import PEOPLE, Plant, build_drop
from helpers import AGENT, CONTROLLER, HR_OPS

KEY = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "answer_key.json").read_text(encoding="utf-8"))
NUMBERS = {person["legal_name"]: person["employee_number"] for person in PEOPLE}


@pytest.fixture()
def june(client) -> list[dict]:
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    response = client.post("/reconciliations?period=2026-06", headers=AGENT)
    assert response.status_code == 200, response.text
    return response.json()["findings"]


def _conflicts(findings: list[dict]) -> list[dict]:
    return [row for row in findings if row["disposition"] == "conflict"]


@pytest.mark.parametrize("anomaly", [row for row in KEY["anomalies"] if row["subjects"]], ids=lambda row: row["id"])
def test_each_planted_case_is_found_as_the_key_expects(june, anomaly):
    for subject in anomaly["subjects"]:
        matches = [row for row in _conflicts(june) if row["kind"] == anomaly["kind"] and row["subject_number"] == subject]
        assert len(matches) == 1, f"{anomaly['id']} {anomaly['kind']} {subject}: {[(r['kind'], r['subject_number']) for r in june]}"
        finding = matches[0]
        assert anomaly["ask"] in finding["ask"]
        for extra in anomaly.get("also_ask", []):
            assert extra in finding["ask"]
        assert finding["draft_allowed"] is anomaly["draft_allowed"]
        assert finding["materiality"] == "escalate"
        assert finding["ranking"] and finding["ranking"][0]["source"] in finding["authority"]
        assert finding["explanation"]
        if anomaly.get("expected_amount") and len(anomaly["subjects"]) == 1:
            assert finding["amount"] == anomaly["expected_amount"]
            assert finding["currency"] == anomaly["expected_currency"]
    if anomaly["id"] == "A03":
        amounts = {row["subject_number"]: (row["amount"], row["currency"]) for row in june if row["kind"] == "forfeiture_still_expensed"}
        assert amounts == {
            item["subject"]: (item["amount"], item["currency"]) for item in anomaly["expected_amounts"]
        }


def test_clean_people_are_never_flagged(june):
    clean = {NUMBERS[name] for name in KEY["true_negatives"] if name in NUMBERS}
    assert clean == {"NL-US-005", "NL-UK-002", "NL-DE-002", "NL-DE-003", "NL-UK-004"}
    flagged = {row["subject_number"] for row in _conflicts(june)}
    assert flagged.isdisjoint(clean)
    fx = next(row for row in june if row["subject_number"] == "GBPUSD")
    assert fx["disposition"] == "explained"


def test_every_conflict_belongs_to_a_planted_case(june):
    """No false alarms: every conflict's subject is a subject of some anomaly in the key."""
    subjects = {subject for row in KEY["anomalies"] for subject in row["subjects"]}
    for row in _conflicts(june):
        assert row["subject_number"] in subjects, (row["kind"], row["subject_number"])


def test_mess_is_explained_not_flagged(june):
    explained = {(row["kind"], row["subject_number"]) for row in june if row["disposition"] == "explained"}
    assert ("duplicate_hr_row", "NL-DE-003") in explained
    assert ("bank_timing", "PAY-2026-06-NL-DE-002") in explained
    assert ("fx_translation", "GBPUSD") in explained
    for row in june:
        if row["disposition"] == "explained":
            assert row["ask"] == []
            assert row["materiality"] == "act_alone"


def test_the_bank_change_rests_on_the_email_the_rule_and_the_march_ruling(june):
    maya = next(row for row in june if row["kind"] == "bank_detail_change")
    kinds = {item["type"]: item for item in maya["basis"]}
    assert kinds["document"]["title"] == "Maya Chen bank change email"
    assert "9911882277" in kinds["document"]["quote"]
    assert kinds["rule"]["id"] == "BNK-1"
    assert kinds["precedent"]["id"] == "P-2026-03-001"
    assert maya["trusted_value"] == "1002003001"
    assert maya["details"]["payments_to_requested_account"] == ["PAY-2026-06-NL-US-001"]
    assert [item["source"] for item in maya["ranking"]] == ["hr", "email"]


def test_a_confirmed_call_back_turns_the_bank_change_into_an_explained_one(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    callback = {"employee_number": "NL-US-001", "account": "9911882277", "outcome": "confirmed", "called_on": "2026-06-28"}
    assert client.post("/controls/callbacks", headers=HR_OPS, json=callback).status_code == 201
    findings = client.post("/reconciliations?period=2026-06", headers=AGENT).json()["findings"]
    maya = next(row for row in findings if row["kind"] == "bank_detail_change")
    assert maya["disposition"] == "explained"
    assert maya["draft_allowed"] is True


def test_the_reconciling_item_for_partial_funding(june):
    lukas = next(row for row in june if row["kind"] == "funding_booked_not_cleared")
    assert lukas["details"]["reconciling_item"] == "6750.00"
    assert [item["source"] for item in lukas["ranking"]][0] == "bank"


def test_detection_runs_both_ways_and_on_a_month_nobody_planned(client):
    """A July drop with a new ghost and an unapproved raise for a clean person: both are found."""
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200

    def ghost(drop) -> None:
        drop.payroll.append({**next(row for row in drop.payroll if row["employee_number"] == "NL-UK-002"),
                             "employee_number": "PAY-0123", "legal_name": "Pat Doe", "destination_account": "2002009999"})

    def owen_raise(drop) -> None:
        from decimal import Decimal

        from app.world.drops import set_gross

        row = next(item for item in drop.payroll if item["employee_number"] == "NL-UK-002")
        set_gross(row, Decimal("7700.00"))

    july = build_drop(
        "2026-07", plants=[Plant("ghost", "sources", ghost), Plant("owen_raise", "sources", owen_raise)], closed=False
    )
    assert client.post("/world/drops", headers=CONTROLLER, json=july).status_code == 200
    findings = client.post("/reconciliations?period=2026-07", headers=AGENT).json()["findings"]
    kinds = {(row["kind"], row["subject_number"]) for row in _conflicts(findings)}
    assert ("unconfirmed_person", "PAY-0123") in kinds
    assert ("pay_above_authorized", "NL-UK-002") in kinds
    owen = next(row for row in findings if row["kind"] == "pay_above_authorized")
    assert owen["amount"] == "700.00"


def test_duplicate_keys_are_compared_after_normalizing():
    assert normalize_key("GR-US-006-DUP") == normalize_key("GR-US-006")
    assert normalize_key(" gr_us_006 copy ") == normalize_key("GR-US-006")
    assert normalize_key("VST-0042") == normalize_key("vst 42")
    assert normalize_key("GR-US-006") != normalize_key("GR-US-016")
