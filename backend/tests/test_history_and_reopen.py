"""Nothing is deleted, numbers never repeat, closed months reopen only through the controller,
and app code reads the world from the database, never from the generator."""

import ast
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import ENGINE
from app.world.drops import MONTHS, build_drop, canonical, fixture_dir
from helpers import AGENT, CONTROLLER

APP = Path(__file__).resolve().parents[1] / "app"
GENERATOR_TRUTH = {"PEOPLE", "SAM_ORTIZ", "assignment", "person_by_number", "hr_view", "ENTITY_RATES", "RATE_CARD"}
MAY_ROLL_OUT_THE_WORLD = {APP / "world" / "drops.py", APP / "world" / "loader.py", APP / "world" / "evidence.py"}


def test_app_code_never_reads_the_generators_truth():
    """Only the generator and the loader may touch the generator. Everything else reads loaded rows."""
    offenders = []
    for path in APP.rglob("*.py"):
        if path in MAY_ROLL_OUT_THE_WORLD:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.world.drops":
                offenders.append(f"{path.relative_to(APP)} imports {[alias.name for alias in node.names]}")
            if isinstance(node, ast.Name) and node.id in GENERATOR_TRUTH:
                offenders.append(f"{path.relative_to(APP)} uses {node.id}")
    assert offenders == []


def test_committed_months_match_the_generator():
    assert sorted(MONTHS) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    for period in MONTHS:
        path = fixture_dir() / f"{period}.json"
        assert path.read_text(encoding="utf-8") == canonical(build_drop(period))


def test_january_to_may_arrive_closed_and_june_is_open(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    for period in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05"):
        for entity in ("US", "UK", "DE"):
            assert client.get(f"/entities/{entity}/periods/{period}", headers=CONTROLLER).json()["status"] == "locked"
    assert client.get("/entities/US/periods/2026-06", headers=CONTROLLER).json()["status"] == "open"


def test_a_drop_loads_once_and_a_different_one_is_refused(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    july = build_drop("2026-07", plants=[], closed=False)
    assert client.post("/world/drops", headers=AGENT, json=july).status_code == 403
    first = client.post("/world/drops", headers=CONTROLLER, json=july)
    assert first.json() == {"status": "loaded", "period": "2026-07", "closed": False}
    assert client.post("/world/drops", headers=CONTROLLER, json=july).json()["status"] == "already_loaded"
    changed = {**july, "received_on": "2026-08-03"}
    refused = client.post("/world/drops", headers=CONTROLLER, json=changed)
    assert refused.status_code == 409
    assert refused.json()["error"] == "drop_conflict"
    assert client.post("/world/drops", headers=CONTROLLER, json={"period": "2026-08"}).status_code == 422


def test_payroll_then_equity_then_payroll_again(client):
    """A re-run used to reuse a journal number and fail. Numbers now never repeat."""
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    first = client.post("/payroll/drafts?period=2026-06", headers=AGENT)
    assert first.status_code == 200, first.text
    assert client.post("/equity/drafts?period=2026-06", headers=AGENT).status_code == 200
    again = client.post("/payroll/drafts?period=2026-06", headers=AGENT)
    assert again.status_code == 200, again.text
    entries = client.get("/entities/US/journal-entries?period=2026-06", headers=CONTROLLER).json()
    numbers = [entry["number"] for entry in entries]
    assert len(numbers) == len(set(numbers))
    wages = [entry for entry in entries if entry["booking_key"] == "payroll:wages:US:2026-06"]
    assert sorted(entry["status"] for entry in wages) == ["draft", "withdrawn"]


def test_a_withdrawn_draft_is_final(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    first = client.post("/payroll/drafts?period=2026-06", headers=AGENT).json()
    old = next(entry for entry in first["journals"] if entry["booking_key"] == "payroll:wages:US:2026-06")
    client.post("/payroll/drafts?period=2026-06", headers=AGENT)
    refused = client.post(f"/journal-entries/{old['id']}/approve", headers=CONTROLLER)
    assert refused.status_code == 409
    for statement in (
        f"UPDATE journal_entries SET status = 'draft' WHERE id = '{old['id']}'",
        f"DELETE FROM journal_entries WHERE id = '{old['id']}'",
        f"UPDATE journal_lines SET debit = debit + 1 WHERE entry_id = '{old['id']}' AND debit > 0",
    ):
        with pytest.raises(Exception, match="journal_is_final"):
            with ENGINE.begin() as connection:
                connection.execute(text(statement))


def test_sqlite_refuses_to_approve_an_unbalanced_entry(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    draft = client.post(
        "/journal-entries",
        headers=AGENT,
        json={
            "entity_code": "US",
            "period": "2026-06",
            "memo": "Balanced when drafted",
            "lines": [
                {"account_code": "5000", "debit": "50.00", "credit": "0.00", "department": "G&A"},
                {"account_code": "2100", "debit": "0.00", "credit": "50.00"},
            ],
        },
    ).json()
    with ENGINE.begin() as connection:
        connection.execute(
            text("UPDATE journal_lines SET debit = 60.00 WHERE entry_id = :id AND debit > 0"), {"id": draft["id"]}
        )
    refused = client.post(f"/journal-entries/{draft['id']}/approve", headers=CONTROLLER)
    assert refused.status_code == 422
    assert refused.json()["error"] == "unbalanced_journal"


def test_only_the_controller_reopens_a_closed_month_and_always_with_a_reason(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    url = "/entities/US/periods/2026-03/reopen"
    assert client.post(url, headers=AGENT, json={"reason": "Priya's corrected date"}).status_code == 403
    assert client.post(url, headers=CONTROLLER, json={"reason": ""}).status_code == 422
    assert client.post(url, headers=CONTROLLER, json={"reason": "   "}).status_code == 422
    assert client.post("/entities/US/periods/2026-06/reopen", headers=CONTROLLER, json={"reason": "x"}).status_code == 409

    reopened = client.post(url, headers=CONTROLLER, json={"reason": "HR corrected Priya's last day to 10 March."})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "reopened"
    assert reopened.json()["reopened_by"] == "dana.ruiz"

    correction = {
        "entity_code": "US",
        "period": "2026-03",
        "memo": "Correction after reopen",
        "lines": [
            {"account_code": "1200", "debit": "20.00", "credit": "0.00", "department": "Product"},
            {"account_code": "5000", "debit": "0.00", "credit": "20.00", "department": "Product"},
        ],
    }
    drafted = client.post("/journal-entries", headers=AGENT, json=correction)
    assert drafted.status_code == 201, drafted.text
    assert client.post(f"/journal-entries/{drafted.json()['id']}/approve", headers=CONTROLLER).status_code == 200
    assert client.post(f"/journal-entries/{drafted.json()['id']}/post", headers=CONTROLLER).status_code == 200

    relocked = client.post("/entities/US/periods/2026-03/lock", headers=CONTROLLER)
    assert relocked.json()["status"] == "locked"
    assert relocked.json()["reopen_reason"] == "HR corrected Priya's last day to 10 March."
    assert client.post("/journal-entries", headers=AGENT, json=correction).status_code == 409
