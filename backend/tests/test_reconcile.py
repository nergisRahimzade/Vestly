from dataclasses import FrozenInstanceError
from helpers import AGENT, CONTROLLER, STRANGER



def _named(rows: list[dict], name: str) -> dict:
    return next(row for row in rows if row["subject_name"] == name)


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
    march_names = {row["subject_name"] for row in march.json()}
    assert "Hannah Price" in march_names
    assert "Priya Shah" not in march_names
    assert "Riley Nguyen" not in march_names

    june = client.post("/reconciliations?period=2026-06", headers=AGENT)
    assert june.status_code == 200, june.text
    rows = june.json()
    priya = _named(rows, "Priya Shah")
    assert priya["fact"] == "employment_dates"
    assert priya["disposition"] == "conflict"
    assert priya["authority"] == "hr"
    assert priya["trusted_value"] == "2026-03-12"
    assert [item["source"] for item in priya["ranking"]] == ["hr", "payroll", "equity", "ledger"]
    assert "2026-03-15" in priya["ranking"][1]["claim"]
    assert "vesting" in priya["ranking"][2]["claim"]
    assert "5200" in priya["ranking"][3]["claim"]
    assert "2026-04-02" in priya["explanation"]
    assert priya["policy_version"] == "northlight-2026-06"

    hannah = _named(rows, "Hannah Price")
    assert hannah["disposition"] == "conflict"
    assert hannah["trusted_value"] == "2026-01-15"
    assert "No payroll register row" in hannah["ranking"][1]["claim"]

    fx = _named(rows, "GBPUSD")
    assert fx["fact"] == "gbp_usd_translation"
    assert fx["disposition"] == "explained"
    assert fx["trusted_value"] == "1.2750"
    assert "1.2700" in fx["explanation"]
    assert "inside the policy band" in fx["explanation"]
    assert "Riley Nguyen" not in {row["subject_name"] for row in rows}

    again = client.post("/reconciliations?period=2026-06", headers=CONTROLLER)
    priya_rows = [row for row in again.json() if row["subject_name"] == "Priya Shah"]
    assert len(priya_rows) == 1

    hidden = client.get("/reconciliations?period=2026-06", headers=STRANGER)
    assert hidden.status_code == 403


def test_reconcile_waits_for_the_file_drops(client):
    response = client.post("/reconciliations?period=2026-06", headers=CONTROLLER)
    assert response.status_code == 409
    assert response.json()["error"] == "world_not_loaded"
