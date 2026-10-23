from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import ENGINE
from helpers import AGENT, CONTROLLER, STRANGER



def _named(rows: list[dict], name: str, fact: str | None = None) -> dict:
    return next(row for row in rows if row["subject_name"] == name and (fact is None or row["fact"] == fact))


def test_policy_is_readable_and_not_editable(client):
    from app.policy.loader import load_policy

    loaded = load_policy()
    try:
        loaded.version = "changed"
        raise AssertionError("policy object accepted a write")
    except FrozenInstanceError:
        pass
    body = client.get("/policy").json()
    assert body["version"] == "northlight-2026-06"
    assert body["content_hash"] == loaded.content_hash
    assert body["contributions"] == {"US": "0.04", "UK": "0.05", "DE": "0.20"}
    facts = {fact["fact"]: fact for fact in body["facts"]}
    assert facts["employment_dates"]["authority"] == "hr"
    assert [item["source"] for item in facts["employment_dates"]["ranking"]] == [
        "hr",
        "payroll",
        "equity",
        "ledger",
    ]
    assert facts["pay_destination"]["email_is_source"] is False
    assert client.post("/policy", headers=CONTROLLER).status_code == 405


def test_priya_conflict_and_in_band_fx(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    missing = client.post("/reconciliations?period=2026-13", headers=CONTROLLER)
    assert missing.status_code == 422

    march = client.post("/reconciliations?period=2026-03", headers=CONTROLLER)
    assert march.status_code == 200, march.text
    march_names = {row["subject_name"] for row in march.json()["findings"]}
    assert "Hannah Price" in march_names
    assert "Priya Shah" not in march_names
    assert "Riley Nguyen" not in march_names

    june = client.post("/reconciliations?period=2026-06", headers=AGENT)
    assert june.status_code == 200, june.text
    rows = june.json()["findings"]
    priya = _named(rows, "Priya Shah", "employment_dates")
    assert priya["kind"] == "late_termination"
    assert priya["ask"] == ["lee.park"]
    assert priya["amount"] == "1451.61"
    assert priya["disposition"] == "conflict"
    assert priya["authority"] == "hr"
    assert priya["trusted_value"] == "2026-03-12"
    assert [item["source"] for item in priya["ranking"]] == ["hr", "payroll", "equity", "ledger"]
    assert "2026-03-15" in priya["ranking"][1]["claim"]
    assert "vesting" in priya["ranking"][2]["claim"]
    assert "5200" in priya["ranking"][3]["claim"]
    assert "2026-04-02" in priya["explanation"]
    assert priya["policy_version"] == "northlight-2026-06"

    hannah = _named(rows, "Hannah Price", "employment_dates")
    assert hannah["disposition"] == "conflict"
    assert hannah["trusted_value"] == "2026-01-15"
    assert "No payroll register row" in hannah["ranking"][1]["claim"]

    fx = _named(rows, "GBPUSD")
    assert fx["fact"] == "gbp_usd_translation"
    assert fx["disposition"] == "explained"
    # Real rates (D20): Open Exchange Rates GBPUSD at 30 June 2026, and the management pack 0.4% under it.
    # At the simulated rates these were 1.2750 and 1.2700.
    assert fx["trusted_value"] == "1.324995"
    assert "1.3197" in fx["explanation"]
    assert "inside the policy band" in fx["explanation"]
    # Riley the person is clean. His June payment line is part of the half-cleared batch (A08), a different subject.
    assert "NL-US-005" not in {row["subject_number"] for row in rows}
    assert next(row for row in rows if row["subject_number"] == "PAY-2026-06-NL-US-005")["kind"] == "uncleared_funding"

    again = client.post("/reconciliations?period=2026-06", headers=CONTROLLER)
    priya_rows = [
        row for row in again.json()["findings"] if row["subject_name"] == "Priya Shah" and row["fact"] == "employment_dates"
    ]
    assert len(priya_rows) == 1

    hidden = client.get("/reconciliations?period=2026-06", headers=STRANGER)
    assert hidden.status_code == 403


def test_reconcile_waits_for_the_file_drops(client):
    response = client.post("/reconciliations?period=2026-06", headers=CONTROLLER)
    assert response.status_code == 409
    assert response.json()["error"] == "world_not_loaded"


def test_every_run_is_kept(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    first = client.post("/reconciliations?period=2026-06", headers=AGENT).json()
    second = client.post("/reconciliations?period=2026-06", headers=CONTROLLER).json()
    assert first["run"]["sequence"] == 1
    assert second["run"]["sequence"] == 2
    assert first["run"]["ran_by"] == "vestly.payroll"
    assert second["run"]["ran_by"] == "dana.ruiz"
    assert first["run"]["inputs_hash"] == second["run"]["inputs_hash"]
    assert first["run"]["policy_hash"] == second["run"]["policy_hash"]
    periods = {doc["period"] for doc in first["run"]["input_documents"]}
    assert periods == {"static", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"}

    runs = client.get("/reconciliations/runs?period=2026-06", headers=AGENT).json()
    assert [run["sequence"] for run in runs] == [1, 2]
    latest = client.get("/reconciliations?period=2026-06", headers=AGENT).json()
    assert latest["run"]["id"] == second["run"]["id"]
    earlier = client.get(f"/reconciliations?period=2026-06&run_id={first['run']['id']}", headers=AGENT).json()
    assert earlier["run"]["id"] == first["run"]["id"]
    assert {row["subject_name"] for row in earlier["findings"]} == {row["subject_name"] for row in first["findings"]}
    assert client.get("/reconciliations?period=2026-05", headers=AGENT).status_code == 404

    for statement in (
        "UPDATE reconciliations SET trusted_value = 'changed'",
        "DELETE FROM reconciliations",
        "UPDATE reconciliation_runs SET ran_by = 'someone else'",
        "DELETE FROM reconciliation_runs",
    ):
        with pytest.raises(IntegrityError, match="append_only"):
            with ENGINE.begin() as connection:
                connection.execute(text(statement))
