import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import ENGINE

CONTROLLER = {"X-Role": "controller", "X-Actor": "controller"}
AGENT = {"X-Role": "vestly_agent", "X-Actor": "vestly"}

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ledger_cases.json"

WAGES = {
    "entity_code": "US",
    "period": "2026-03",
    "memo": "US Engineering wages for March 2026",
    "lines": [
        {"account_code": "5000", "debit": "9000.00", "credit": "0.00", "department": "Engineering"},
        {"account_code": "2100", "debit": "0.00", "credit": "9000.00"},
    ],
}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "vestly-ledger"


def test_chart_and_entities(client):
    chart = client.get("/chart")
    assert chart.status_code == 200
    assert {account["code"] for account in chart.json()} == {
        "1000",
        "1100",
        "2100",
        "2200",
        "2210",
        "2220",
        "3000",
        "5000",
        "5100",
        "5110",
        "5200",
        "6100",
    }
    entities = client.get("/entities")
    assert {(row["code"], row["currency"]) for row in entities.json()} == {
        ("US", "USD"),
        ("UK", "GBP"),
        ("DE", "EUR"),
    }


def test_foundation_fixture(client):
    spec = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for step in spec["steps"]:
        action = step["action"]
        if action == "open_period":
            response = client.post(
                f"/entities/{step['entity']}/periods/{step['period']}",
                headers=CONTROLLER,
            )
            assert response.status_code == 201, response.text
        elif action == "lock_period":
            response = client.post(
                f"/entities/{step['entity']}/periods/{step['period']}/lock",
                headers=CONTROLLER,
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "locked"
        elif action == "post":
            response = client.post("/journal-entries", headers=CONTROLLER, json=step)
            expect = step["expect"]
            assert response.status_code == expect["status"], step["name"] + " " + response.text
            body = response.json()
            if "error" in expect:
                assert body["error"] == expect["error"]
            else:
                assert body["total_debit"] == expect["total"]
                assert body["total_credit"] == expect["total"]
                assert body["currency"] == "USD"
                assert body["status"] == "posted"
        else:
            raise AssertionError(f"Unknown step {action}")

    listed = client.get(f"/entities/{spec['entity']}/journal-entries?period={spec['period']}")
    assert listed.status_code == 200
    entries = listed.json()
    assert len(entries) == spec["expected_posted_entries"]
    assert entries[0]["number"] == "JE-US-2026-03-0001"
    assert entries[0]["total_debit"] == entries[0]["total_credit"]


def test_agent_cannot_post_or_lock(client):
    opened = client.post("/entities/US/periods/2026-03", headers=CONTROLLER)
    assert opened.status_code == 201
    denied_lock = client.post("/entities/US/periods/2026-03/lock", headers=AGENT)
    assert denied_lock.status_code == 403
    assert denied_lock.json()["error"] == "forbidden"
    denied_post = client.post("/journal-entries", headers=AGENT, json=WAGES)
    assert denied_post.status_code == 403
    assert denied_post.json()["error"] == "forbidden"
    period = client.get("/entities/US/periods/2026-03")
    assert period.json()["status"] == "open"
    listed = client.get("/entities/US/journal-entries?period=2026-03")
    assert listed.json() == []


def test_books_stay_inside_the_entity(client):
    assert client.post("/entities/US/periods/2026-03", headers=CONTROLLER).status_code == 201
    assert client.post("/entities/UK/periods/2026-03", headers=CONTROLLER).status_code == 201
    us = client.post("/journal-entries", headers=CONTROLLER, json=WAGES)
    uk_wages = {
        **WAGES,
        "entity_code": "UK",
        "memo": "UK Engineering wages for March 2026",
    }
    uk = client.post("/journal-entries", headers=CONTROLLER, json=uk_wages)
    assert us.status_code == 201
    assert uk.status_code == 201
    assert us.json()["currency"] == "USD"
    assert uk.json()["currency"] == "GBP"
    us_books = client.get("/entities/US/journal-entries?period=2026-03").json()
    uk_books = client.get("/entities/UK/journal-entries?period=2026-03").json()
    assert [entry["number"] for entry in us_books] == ["JE-US-2026-03-0001"]
    assert [entry["number"] for entry in uk_books] == ["JE-UK-2026-03-0001"]


def test_unknown_account_and_unopened_period(client):
    missing = client.post("/journal-entries", headers=CONTROLLER, json=WAGES)
    assert missing.status_code == 422
    assert missing.json()["error"] == "period_not_open"
    assert client.post("/entities/US/periods/2026-03", headers=CONTROLLER).status_code == 201
    bad_account = {
        **WAGES,
        "lines": [
            {"account_code": "9999", "debit": "10.00", "credit": "0.00"},
            {"account_code": "2100", "debit": "0.00", "credit": "10.00"},
        ],
    }
    rejected = client.post("/journal-entries", headers=CONTROLLER, json=bad_account)
    assert rejected.status_code == 422
    assert rejected.json()["error"] == "unknown_account"


def test_database_rejects_a_locked_period(client):
    assert client.post("/entities/UK/periods/2026-03", headers=CONTROLLER).status_code == 201
    assert client.post("/entities/UK/periods/2026-03/lock", headers=CONTROLLER).status_code == 200
    with pytest.raises(IntegrityError, match="period_locked"):
        with ENGINE.begin() as connection:
            period = connection.execute(
                text(
                    """
                    SELECT p.id AS period_id, e.id AS entity_id
                    FROM periods p
                    JOIN entities e ON e.id = p.entity_id
                    WHERE e.code = 'UK'
                    """
                )
            ).one()
            connection.execute(
                text(
                    """
                    INSERT INTO journal_entries
                      (id, entity_id, period_id, number, memo, status, currency, posted_by, posted_at)
                    VALUES
                      (:id, :entity_id, :period_id, 'JE-UK-RAW', 'bypass the service', 'posted', 'GBP', 'controller', :posted_at)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "entity_id": period.entity_id,
                    "period_id": period.period_id,
                    "posted_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
