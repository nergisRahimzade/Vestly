from decimal import Decimal

from app.equity.schedule import load_signed_grants, schedule_for
from app.world.drops import grants_dir
from helpers import AGENT, CONTROLLER, STRANGER


def _named(rows: list[dict], name: str, period: str | None = None) -> dict:
    return next(
        row
        for row in rows
        if row["legal_name"] == name and (period is None or row["period"] == period)
    )


def _journal(rows: list[dict], text: str) -> dict:
    return next(row for row in rows if text in row["memo"])


def _line(entry: dict, account: str, department: str | None = None) -> dict:
    matches = [
        line
        for line in entry["lines"]
        if line["account_code"] == account and (department is None or line["department"] == department)
    ]
    assert matches, f"no {account} {department} in {entry['memo']}"
    return matches[0]


def _grant(number: str):
    return next(grant for grant in load_signed_grants(grants_dir()) if grant.grant_number == number)


def test_cumulative_never_falls_below_vested():
    for grant in load_signed_grants(grants_dir()):
        for row in schedule_for(grant, "2026-06"):
            assert row.cumulative_expense + Decimal("0.01") >= row.vested_value


def test_hannah_reversal_equals_all_expense_and_a_2024_grant_still_runs():
    hannah = schedule_for(_grant("GR-UK-003"), "2026-06")
    january = next(row for row in hannah if row.period == "2026-01")
    recognized = sum((row.monthly_expense for row in hannah if row.period <= "2026-01"), Decimal("0.00"))
    assert january.status == "forfeited"
    assert january.reversal == recognized == Decimal("3000.00")
    assert january.net_expense == Decimal("-2500.00")
    june = next(row for row in hannah if row.period == "2026-06")
    assert june.net_expense == Decimal("0.00")
    assert june.cumulative_expense == Decimal("0.00")

    priya = schedule_for(_grant("GR-US-003"), "2026-06")
    assert any(row.period.startswith("2026-") for row in priya)
    march = next(row for row in priya if row.period == "2026-03")
    assert march.status == "forfeited"
    assert march.service_month > 12
    assert march.monthly_expense == Decimal("1000.00")
    june_priya = next(row for row in priya if row.period == "2026-06")
    assert june_priya.net_expense == Decimal("0.00")


def test_june_equity_drafts(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    locked = client.post("/equity/drafts?period=2026-03", headers=AGENT)
    assert locked.status_code == 409
    assert locked.json()["error"] == "period_locked"
    denied = client.post("/equity/drafts?period=2026-06", headers=CONTROLLER)
    assert denied.status_code == 403

    schedules = client.get("/equity/schedules?period=2026-06", headers=AGENT)
    assert schedules.status_code == 200, schedules.text
    rows = schedules.json()["schedules"]
    assert all(Decimal(row["cumulative_expense"]) + Decimal("0.01") >= Decimal(row["vested_value"]) for row in rows)
    assert "GR-US-006-DUP" not in {row["grant_number"] for row in rows}

    priya = _named(rows, "Priya Shah", "2026-06")
    assert priya["net_expense"] == "0.00"
    assert priya["status"] == "forfeited"

    hannah = _named(rows, "Hannah Price", "2026-01")
    recognized = sum(
        Decimal(row["monthly_expense"])
        for row in rows
        if row["grant_number"] == "GR-UK-003" and row["period"] <= "2026-01"
    )
    assert Decimal(hannah["reversal"]) == recognized
    assert _named(rows, "Hannah Price", "2026-06")["net_expense"] == "0.00"

    lukas = _named(rows, "Lukas Weber", "2026-06")
    assert lukas["board_approved"] is False
    assert lukas["net_expense"] == "0.00"
    assert lukas["status"] == "pending_approval"

    tomas = _named(rows, "Tomas Fischer", "2026-06")
    assert tomas["entity_code"] == "UK"
    assert tomas["department"] == "Engineering"
    assert tomas["net_expense"] == "500.00"

    riley = _named(rows, "Riley Nguyen", "2026-06")
    assert riley["status"] == "vesting"
    assert riley["net_expense"] == "750.00"
    assert riley["entity_code"] == "US"

    jonah = _named(rows, "Jonah Hale", "2026-06")
    assert jonah["vest_end"] == "2028-03-01"
    assert jonah["net_expense"] == "500.00"
    assert any(row["grant_number"] == "GR-US-002" and row["period"] == "2026-06" for row in rows)

    hidden = client.get("/equity/schedules?period=2026-06", headers=STRANGER)
    assert hidden.status_code == 403

    response = client.post("/equity/drafts?period=2026-06", headers=AGENT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(entry["balanced"] and entry["status"] == "draft" for entry in body["journals"])
    assert all(entry["prepared_by"] == "vestly" for entry in body["journals"])
    assert _named(body["schedules"], "Priya Shah")["net_expense"] == "0.00"
    assert _named(body["schedules"], "Hannah Price")["net_expense"] == "0.00"

    lukas_note = next(note for note in body["notes"] if note["kind"] == "missing_grant_approval")
    assert lukas_note["legal_name"] == "Lukas Weber"
    jonah_note = next(note for note in body["notes"] if note["kind"] == "vest_end_mismatch")
    assert jonah_note["signed_vest_end"] == "2028-03-01"
    assert jonah_note["platform_vest_end"] == "2027-03-01"

    us = _journal(body["journals"], "US stock compensation")
    assert _line(us, "5200", "Engineering")["debit"] == "2250.00"
    assert _line(us, "5200", "Product")["debit"] == "500.00"
    assert _line(us, "5200", "GTM")["debit"] == "500.00"
    assert _line(us, "3000")["credit"] == "3250.00"

    uk = _journal(body["journals"], "UK stock compensation")
    assert _line(uk, "5200", "Engineering")["debit"] == "1500.00"
    assert _line(uk, "5200", "GTM")["debit"] == "500.00"
    assert all(line["department"] != "G&A" for line in uk["lines"] if line["account_code"] == "5200")

    de = _journal(body["journals"], "DE stock compensation")
    assert _line(de, "5200", "Product")["debit"] == "500.00"
    assert _line(de, "5200", "G&A")["debit"] == "500.00"
    assert all(line["department"] != "Engineering" for line in de["lines"] if line["account_code"] == "5200")

    again = client.post("/equity/drafts?period=2026-06", headers=AGENT)
    assert again.status_code == 200
    us_drafts = [
        entry
        for entry in client.get("/entities/US/journal-entries?period=2026-06", headers=CONTROLLER).json()
        if entry["status"] == "draft" and "[Equity]" in entry["memo"]
    ]
    assert len(us_drafts) == 1


def test_reconcile_uses_the_subledger_not_a_department_shortcut(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    june = client.post("/reconciliations?period=2026-06", headers=AGENT)
    assert june.status_code == 200, june.text
    priya = next(row for row in june.json()["findings"] if row["subject_name"] == "Priya Shah")
    ledger = priya["ranking"][3]["claim"]
    assert "5200" in ledger
    assert "only" not in ledger
    assert "GR-US-003" in ledger
    assert "0.00" in ledger
    equity = priya["ranking"][2]["claim"]
    assert "vesting" in equity
    assert "forfeited" in equity
