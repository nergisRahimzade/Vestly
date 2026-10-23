from decimal import Decimal

from app.payroll.calc import overpaid_gross
from helpers import AGENT, CONTROLLER


def riley_hand_calc() -> dict[str, str]:
    """Riley Nguyen, US Engineering, June, worked by hand: gross 10000.00 at the policy's US rates
    (withholding 20%, employer tax 8%, 401(k) match 4%). Kept here so the code is checked against
    arithmetic it does not share."""
    return {
        "gross_wages": "10000.00",
        "employee_withholdings": "2000.00",
        "net_pay": "8000.00",
        "employer_tax": "800.00",
        "employer_contribution": "400.00",
    }


def _named(rows: list[dict], name: str) -> dict:
    return next(row for row in rows if row["legal_name"] == name)


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


def test_riley_hand_calc_matches_policy_rates():
    assert riley_hand_calc() == {
        "gross_wages": "10000.00",
        "employee_withholdings": "2000.00",
        "net_pay": "8000.00",
        "employer_tax": "800.00",
        "employer_contribution": "400.00",
    }
    assert overpaid_gross(Decimal("7500.00"), "2026-03-01", "2026-03-15", "2026-03-12") == Decimal("1500.00")


def test_june_payroll_drafts(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    locked = client.post("/payroll/drafts?period=2026-03", headers=AGENT)
    assert locked.status_code == 409
    assert locked.json()["error"] == "period_locked"
    denied = client.post("/payroll/drafts?period=2026-06", headers=CONTROLLER)
    assert denied.status_code == 403

    response = client.post("/payroll/drafts?period=2026-06", headers=AGENT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert all(entry["balanced"] and entry["status"] == "draft" for entry in body["journals"])
    assert all(entry["prepared_by"] == "vestly.payroll" for entry in body["journals"])

    riley = _named(body["people"], "Riley Nguyen")
    assert riley == {
        **riley_hand_calc(),
        "employee_number": "NL-US-005",
        "legal_name": "Riley Nguyen",
        "entity_code": "US",
        "department": "Engineering",
        "register_contribution": "400.00",
        "contribution_difference": "0.00",
        "held_gross": "0.00",
    }

    amira = _named(body["people"], "Amira Khan")
    assert amira["employer_contribution"] == "400.00"
    assert amira["register_contribution"] == "240.00"
    assert amira["contribution_difference"] == "160.00"
    amira_note = next(note for note in body["notes"] if note["kind"] == "contribution_rate_break")
    assert amira_note["legal_name"] == "Amira Khan"
    assert amira_note["ask"] == "payroll.provider"

    ines = _named(body["people"], "Ines Duarte")
    assert ines["gross_wages"] == "4800.00"
    assert ines["employer_contribution"] == "240.00"

    held = {row["legal_name"]: row for row in body["held"]}
    assert held["Evan Brooks"]["gross_held"] == "4000.00"
    assert held["Evan Brooks"]["kind"] == "closed_period_effect"
    assert _named(body["people"], "Evan Brooks")["gross_wages"] == "12000.00"
    assert held["Tomas Fischer"]["gross_held"] == "8500.00"
    assert held["Tomas Fischer"]["entity_code"] == "DE"
    assert held["Tomas Fischer"]["kind"] == "transfer_not_reflected"
    assert set(held) == {"Evan Brooks", "Tomas Fischer"}
    names = {row["legal_name"] for row in body["people"]}
    assert "Tomas Fischer" not in names
    assert "Sam Ortiz" not in names
    assert "Priya Shah" not in names
    ghost = next(note for note in body["notes"] if note["kind"] == "unconfirmed_person")
    assert ghost["employee_number"] == "PAY-0099"

    us_wages = _journal(body["journals"], "US wages")
    engineering = _line(us_wages, "5000", "Engineering")
    assert engineering["debit"] == "37000.00"
    contrib = _line(us_wages, "2220")
    # Evan's 4,000.00 retro lump is held, so his 401(k) match is drafted on 12,000.00, not 16,000.00.
    assert contrib["credit"] == "2400.00"

    uk_wages = _journal(body["journals"], "UK wages")
    amira_contrib_line = _line(uk_wages, "2220")
    assert amira_contrib_line["credit"] == "990.00"

    overpay = _journal(body["journals"], "Overpayment receivable")
    # Paid 1-15 March at 15,000.00 a month (7,258.06); the 3 days after 12 March are overpaid.
    assert _line(overpay, "1200", "Product")["debit"] == "1451.61"
    assert _line(overpay, "5000", "Product")["credit"] == "1451.61"
    priya = next(note for note in body["notes"] if note["kind"] == "overpayment")
    assert priya["legal_name"] == "Priya Shah"
    assert priya["paid_through"] == "2026-03-15"

    suspense = _journal(body["journals"], "suspense")
    assert _line(suspense, "1210")["debit"] == "6400.00"
    assert _line(suspense, "1000")["credit"] == "6400.00"

    accrual = _journal(body["journals"], "UK month-end accrual")
    assert Decimal(_line(accrual, "2150")["credit"]) > 0
    assert Decimal(_line(accrual, "5000", "Product")["debit"]) == Decimal("1033.33")

    again = client.post("/payroll/drafts?period=2026-06", headers=AGENT)
    us_drafts = [
        entry
        for entry in client.get("/entities/US/journal-entries?period=2026-06", headers=CONTROLLER).json()
        if entry["status"] == "draft" and "wages" in entry["memo"]
    ]
    assert len(us_drafts) == 1
    assert again.json()["journals"]


def test_july_reverses_the_june_uk_accrual(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    june = client.post("/payroll/drafts?period=2026-06", headers=AGENT).json()
    accrual = _journal(june["journals"], "UK month-end accrual")
    for entity in ("US", "UK", "DE"):
        assert client.post(f"/entities/{entity}/periods/2026-07", headers=CONTROLLER).status_code == 201
    july = client.post("/payroll/drafts?period=2026-07", headers=AGENT)
    assert july.status_code == 200, july.text
    reversal = _journal(july.json()["journals"], "UK accrual reversal")
    assert reversal["status"] == "draft"
    wages = Decimal(_line(accrual, "2150")["credit"])
    assert _line(reversal, "2150")["debit"] == f"{wages:.2f}"
    assert _line(reversal, "5000", "Product")["credit"] == _line(accrual, "5000", "Product")["debit"]
    assert "Sam Ortiz" not in {note.get("legal_name") for note in july.json()["notes"]}
