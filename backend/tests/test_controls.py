"""The policy gate and the approvals inbox: rules the agent cannot talk its way past."""

from sqlalchemy import select, text

from app.db import ENGINE, SessionLocal
from app.equity.models import EquityGrantMonth
from app.world.models import PayrollRegisterRow
from helpers import (
    AGENT,
    AUDITOR,
    CONTROLLER,
    EQUITY_AGENT,
    FINANCE_MGR,
    HR_COMP,
    HR_OPS,
    PAYROLL_AGENT,
    STRANGER,
    as_user,
)

ACCRUAL_62K = {
    "entity_code": "US",
    "period": "2026-06",
    "memo": "US payroll accrual for June 2026 bonuses",
    "lines": [
        {"account_code": "5000", "debit": "62000.00", "credit": "0.00", "department": "GTM"},
        {"account_code": "2150", "debit": "0.00", "credit": "62000.00"},
    ],
}
SMALL = {
    "entity_code": "US",
    "period": "2026-06",
    "memo": "US G&A wage correction",
    "lines": [
        {"account_code": "5000", "debit": "120.00", "credit": "0.00", "department": "G&A"},
        {"account_code": "2100", "debit": "0.00", "credit": "120.00"},
    ],
}


def _load(client) -> None:
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200


def _draft(client, payload: dict) -> dict:
    response = client.post("/journal-entries", headers=AGENT, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _request_for(client, entry_id: str, status: str = "pending") -> dict:
    rows = client.get(f"/approvals?subject_id={entry_id}&status={status}", headers=AUDITOR).json()
    assert len(rows) == 1, rows
    return rows[0]


def _refusals(client, rule: str) -> list[dict]:
    events = client.get("/audit/events?action=refused&limit=1000", headers=AUDITOR).json()
    return [event for event in events if event["reason_code"] == rule]


def test_the_rules_are_in_the_policy_file(client):
    policy = client.get("/policy").json()
    assert {"ACC-1", "SOD-1", "SOD-2", "LIM-1", "BND-1", "BNK-1", "GRT-1", "CTR-1", "DUP-1", "CLS-1", "PRV-1"} <= set(
        policy["rules"]
    )
    assert "approve_entry" not in policy["roles"]["agent"]
    assert "release_funding" not in policy["roles"]["agent"]
    assert policy["approval_limit"] == "25000.00"


def test_the_agent_cannot_approve_post_release_or_reopen(client):
    _load(client)
    entry = _draft(client, SMALL)
    approve = client.post(f"/journal-entries/{entry['id']}/approve", headers=AGENT)
    assert (approve.status_code, approve.json()["rule"]) == (403, "SOD-1")
    post = client.post(f"/journal-entries/{entry['id']}/post", headers=AGENT)
    assert (post.status_code, post.json()["rule"]) == (403, "ACC-1")
    reopen = client.post("/entities/US/periods/2026-03/reopen", headers=AGENT, json={"reason": "please"})
    assert (reopen.status_code, reopen.json()["rule"]) == (403, "ACC-1")
    for action in ("release_funding", "approve_entry", "reopen_period", "post_entry"):
        verdict = client.post("/controls/check", headers=AGENT, json={"action": action}).json()
        assert verdict["allowed"] is False and verdict["rule"] == "ACC-1"
    assert client.post("/controls/check", headers=AGENT, json={"action": "propose_entry"}).json()["allowed"] is True
    assert client.post("/clock/advance?days=1", headers=AGENT).json()["rule"] == "ACC-1"

    # What Vestly tried and what stopped it is in the record.
    assert _refusals(client, "SOD-1")
    assert {event["actor"] for event in _refusals(client, "ACC-1")} >= {"vestly.payroll"}


def test_a_62000_self_approval_is_refused_and_one_approver_is_not_enough(client):
    """Case A13: above the limit, two different people approve, one of them the controller."""
    _load(client)
    entry = _draft(client, ACCRUAL_62K)
    request = _request_for(client, entry["id"])
    assert (request["approvals_needed"], request["needs_controller"]) == (2, True)
    assert request["requested_by"] == "vestly.payroll"

    own = client.post(f"/approvals/{request['id']}/approve", headers=PAYROLL_AGENT)
    assert (own.status_code, own.json()["rule"]) == (403, "SOD-1")

    first = client.post(f"/journal-entries/{entry['id']}/approve", headers=FINANCE_MGR)
    assert first.json()["status"] == "draft"
    again = client.post(f"/journal-entries/{entry['id']}/approve", headers=FINANCE_MGR)
    assert again.status_code == 409
    not_yet = client.post(f"/journal-entries/{entry['id']}/post", headers=CONTROLLER)
    assert not_yet.status_code == 409

    assert request["id"] in {row["id"] for row in client.get("/approvals/inbox", headers=CONTROLLER).json()}
    assert request["id"] not in {row["id"] for row in client.get("/approvals/inbox", headers=FINANCE_MGR).json()}
    second = client.post(f"/journal-entries/{entry['id']}/approve", headers=CONTROLLER)
    assert second.json()["status"] == "approved"
    assert second.json()["reviewed_by"] == "dana.ruiz"
    assert client.post(f"/journal-entries/{entry['id']}/post", headers=CONTROLLER).status_code == 200
    done = _request_for(client, entry["id"], status="approved")
    assert [item["by"] for item in done["decisions"]] == ["chris.lam", "dana.ruiz"]


def test_a_small_entry_needs_one_approver(client):
    _load(client)
    entry = _draft(client, SMALL)
    request = _request_for(client, entry["id"])
    assert (request["approvals_needed"], request["needs_controller"]) == (1, False)
    approved = client.post(f"/journal-entries/{entry['id']}/approve", headers=FINANCE_MGR)
    assert approved.json()["status"] == "approved"


def test_whoever_keyed_a_pay_change_cannot_approve_the_run_that_pays_it(client):
    """SOD-2: sam.kerr keyed Evan's backdated raise, so sam.kerr cannot approve the US June wages."""
    _load(client)
    body = client.post("/payroll/drafts?period=2026-06", headers=PAYROLL_AGENT).json()
    wages = next(entry for entry in body["journals"] if entry["booking_key"] == "payroll:wages:US:2026-06")
    refused = client.post(f"/journal-entries/{wages['id']}/approve", headers=HR_COMP)
    assert (refused.status_code, refused.json()["rule"]) == (403, "SOD-2")
    assert "NL-US-004" in refused.json()["message"]
    other = client.post(f"/journal-entries/{wages['id']}/approve", headers=HR_OPS)
    assert other.json()["rule"] == "ACC-1"
    uk = next(entry for entry in body["journals"] if entry["booking_key"] == "payroll:wages:UK:2026-06")
    assert client.post(f"/journal-entries/{uk['id']}/approve", headers=HR_COMP).json()["rule"] == "ACC-1"


def test_an_edited_draft_loses_its_approval(client):
    """BND-1: an approval covers the exact entry. A change after the first approval voids it."""
    _load(client)
    entry = _draft(client, ACCRUAL_62K)
    assert client.post(f"/journal-entries/{entry['id']}/approve", headers=FINANCE_MGR).status_code == 200
    with ENGINE.begin() as connection:
        connection.execute(
            text("UPDATE journal_lines SET debit = 65000.00 WHERE entry_id = :id AND debit > 0"), {"id": entry["id"]}
        )
        connection.execute(
            text("UPDATE journal_lines SET credit = 65000.00 WHERE entry_id = :id AND credit > 0"), {"id": entry["id"]}
        )
    refused = client.post(f"/journal-entries/{entry['id']}/approve", headers=CONTROLLER)
    assert (refused.status_code, refused.json()["rule"]) == (409, "BND-1")
    voided = _request_for(client, entry["id"], status="voided")
    fresh = _request_for(client, entry["id"])
    assert voided["replaced_by"] == fresh["id"]
    assert fresh["decisions"] == []
    assert fresh["amount"] == "65000.00"

    assert client.post(f"/journal-entries/{entry['id']}/approve", headers=FINANCE_MGR).json()["status"] == "draft"
    assert client.post(f"/journal-entries/{entry['id']}/approve", headers=CONTROLLER).json()["status"] == "approved"


def test_a_decline_needs_a_reason_and_rejects_the_draft(client):
    _load(client)
    entry = _draft(client, SMALL)
    assert client.post(f"/journal-entries/{entry['id']}/reject", headers=FINANCE_MGR, json={"note": " "}).status_code == 422
    declined = client.post(
        f"/journal-entries/{entry['id']}/reject", headers=FINANCE_MGR, json={"note": "Wrong department."}
    )
    assert declined.json()["status"] == "rejected"
    assert _request_for(client, entry["id"], status="declined")["decisions"][0]["note"] == "Wrong department."


def test_an_unanswered_request_is_reminded_then_escalated(client):
    _load(client)
    assert client.get("/clock").json()["today"] == "2026-07-02"
    entry = _draft(client, SMALL)
    request = _request_for(client, entry["id"])
    assert request["inbox_roles"] == ["finance_mgr"]
    assert request["id"] not in {row["id"] for row in client.get("/approvals/inbox", headers=CONTROLLER).json()}

    two_days = client.post("/clock/advance?days=2", headers=CONTROLLER).json()
    assert two_days["today"] == "2026-07-04"
    assert {"request": request["id"], "change": "reminder", "count": 1} in two_days["changes"]
    assert _request_for(client, entry["id"])["escalated"] is False

    four_days = client.post("/clock/advance?days=2", headers=CONTROLLER).json()
    assert {"request": request["id"], "change": "escalated", "to": ["controller"]} in four_days["changes"]
    escalated = _request_for(client, entry["id"])
    assert escalated["escalated"] is True
    assert escalated["inbox_roles"] == ["finance_mgr", "controller"]
    assert request["id"] in {row["id"] for row in client.get("/approvals/inbox", headers=CONTROLLER).json()}
    actions = [event["action"] for event in client.get(f"/audit/events?subject_id={request['id']}", headers=AUDITOR).json()]
    assert actions == ["approval_requested", "approval_reminder", "approval_reminder", "approval_escalated"]


def test_a_bank_change_needs_a_confirmed_call_back_in_the_window(client):
    """BNK-1: Maya's emailed account is refused until HR confirms it by phone within 5 days of the pay run."""
    _load(client)
    maya = {
        "action": "request_funding",
        "employee_number": "NL-US-001",
        "account": "9911882277",
        "period": "2026-06",
        "pay_date": "2026-06-30",
    }

    def verdict() -> dict:
        return client.post("/controls/check", headers=AGENT, json=maya).json()

    assert verdict()["rule"] == "BNK-1"
    assert "no call-back" in verdict()["reason"]
    master = client.post("/controls/check", headers=AGENT, json={**maya, "account": "1002003001"}).json()
    assert master["allowed"] is True

    callback = {"employee_number": "NL-US-001", "account": "9911882277", "outcome": "confirmed", "called_on": "2026-06-20"}
    assert client.post("/controls/callbacks", headers=AGENT, json=callback).json()["rule"] == "ACC-1"
    assert client.post("/controls/callbacks", headers=HR_OPS, json=callback).status_code == 201
    assert verdict()["rule"] == "BNK-1"  # 20 June is outside the five days before 30 June

    denied = {**callback, "outcome": "denied", "called_on": "2026-06-28", "note": "Maya did not send that email."}
    assert client.post("/controls/callbacks", headers=HR_OPS, json=denied).status_code == 201
    assert "did not ask" in verdict()["reason"]

    confirmed = {**callback, "called_on": "2026-06-29"}
    assert client.post("/controls/callbacks", headers=HR_OPS, json=confirmed).status_code == 201
    assert verdict()["allowed"] is True


def test_no_stock_expense_rests_on_an_unapproved_grant(client):
    """GRT-1: a stock-expense debit citing Lukas's grant is refused; his grant has no board approval."""
    _load(client)
    assert client.post("/equity/drafts?period=2026-06", headers=EQUITY_AGENT).status_code == 200
    with SessionLocal() as session:
        lukas = session.scalar(
            select(EquityGrantMonth).where(EquityGrantMonth.grant_number == "GR-DE-001", EquityGrantMonth.period == "2026-06")
        )
    entry = {
        "entity_code": "DE",
        "period": "2026-06",
        "memo": "Lukas stock expense",
        "lines": [
            {
                "account_code": "5200",
                "debit": "1000.00",
                "credit": "0.00",
                "department": "Engineering",
                "sources": [{"table": "equity_grant_months", "id": lukas.id}],
            },
            {"account_code": "3000", "debit": "0.00", "credit": "1000.00"},
        ],
    }
    refused = client.post("/journal-entries", headers=EQUITY_AGENT, json=entry)
    assert (refused.status_code, refused.json()["rule"]) == (422, "GRT-1")


def test_contributions_must_follow_the_policy_rate(client):
    """CTR-1: Amira's register says 3%; an entry booking her pay with a 3% pension line is refused."""
    _load(client)
    with SessionLocal() as session:
        amira = session.scalar(
            select(PayrollRegisterRow).where(
                PayrollRegisterRow.employee_number == "NL-UK-001", PayrollRegisterRow.period == "2026-06"
            )
        )
    source = [{"table": "payroll_register_rows", "id": amira.id}]

    def entry(contribution: str) -> dict:
        total = f"{8000 + float(contribution):.2f}"
        return {
            "entity_code": "UK",
            "period": "2026-06",
            "memo": "Amira pay",
            "lines": [
                {"account_code": "5000", "debit": "8000.00", "credit": "0.00", "department": "Engineering", "sources": source},
                {"account_code": "5110", "debit": contribution, "credit": "0.00", "department": "Engineering", "sources": source},
                {"account_code": "2100", "debit": "0.00", "credit": total},
            ],
        }

    refused = client.post("/journal-entries", headers=AGENT, json=entry("240.00"))
    assert (refused.status_code, refused.json()["rule"]) == (422, "CTR-1")
    assert client.post("/journal-entries", headers=AGENT, json=entry("400.00")).status_code == 201


def test_each_seat_drafts_only_its_own_work(client):
    _load(client)
    payroll_by_equity = client.post("/payroll/drafts?period=2026-06", headers=EQUITY_AGENT)
    assert (payroll_by_equity.status_code, payroll_by_equity.json()["rule"]) == (403, "ACC-1")
    equity_by_payroll = client.post("/equity/drafts?period=2026-06", headers=PAYROLL_AGENT)
    assert equity_by_payroll.json()["rule"] == "ACC-1"
    assert client.post("/payroll/drafts?period=2026-06", headers=PAYROLL_AGENT).status_code == 200


def test_locked_months_and_duplicates_name_their_rules(client):
    _load(client)
    locked = client.post("/journal-entries", headers=AGENT, json={**SMALL, "period": "2026-03"})
    assert (locked.status_code, locked.json()["rule"]) == (409, "CLS-1")
    keyed = {**SMALL, "booking_key": "test:dup"}
    assert client.post("/journal-entries", headers=AGENT, json=keyed).status_code == 201
    dup = client.post("/journal-entries", headers=AGENT, json=keyed)
    assert (dup.status_code, dup.json()["rule"]) == (409, "DUP-1")
    private = client.get("/world/hr?period=2026-06&employee_number=NL-US-001", headers=AGENT)
    assert private.json()["rule"] == "PRV-1"
    assert client.get("/world/hr?period=2026-06", headers=STRANGER).status_code == 403
    assert _refusals(client, "CLS-1") and _refusals(client, "DUP-1") and _refusals(client, "PRV-1")


def test_a_stranger_cannot_reach_the_inbox_controls(client):
    _load(client)
    entry = _draft(client, SMALL)
    request = _request_for(client, entry["id"])
    assert client.post(f"/approvals/{request['id']}/approve", headers=STRANGER).json()["rule"] == "ACC-1"
    assert client.get("/approvals/inbox", headers=STRANGER).json() == []
    assert client.get(f"/approvals/{request['id']}", headers=as_user("head.gtm")).status_code == 403
