"""The subledger follows signed grants and HR as loaded, and June's draft is a true-up, not a second booking."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select

from app.db import SessionLocal
from app.equity.models import EquityGrantMonth, EquityScheduleRun
from app.equity.service import compute_schedules
from app.world.drops import Plant, build_drop
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


def _schedule(through: str):
    with SessionLocal() as session:
        rows, _notes = compute_schedules(session, through)
    return rows


def _booked_5200(client, entity: str, periods: list[str]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for period in periods:
        for entry in client.get(f"/entities/{entity}/journal-entries?period={period}", headers=CONTROLLER).json():
            if entry["status"] not in ("draft", "approved", "posted"):
                continue
            for line in entry["lines"]:
                if line["account_code"] == "5200":
                    totals[line["department"]] += Decimal(line["debit"]) - Decimal(line["credit"])
    return totals


JANUARY_TO_JUNE = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]


def test_cumulative_never_falls_below_vested(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    for row in _schedule("2026-06"):
        assert row.cumulative_expense + Decimal("0.01") >= row.vested_value


def test_hannah_reversal_equals_all_expense_and_a_2024_grant_still_runs(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    rows = _schedule("2026-06")
    hannah = [row for row in rows if row.grant_number == "GR-UK-003"]
    january = next(row for row in hannah if row.period == "2026-01")
    recognized = sum((row.monthly_expense for row in hannah if row.period <= "2026-01"), Decimal("0.00"))
    assert january.status == "forfeited"
    assert january.reversal == recognized == Decimal("3000.00")
    assert january.net_expense == Decimal("-2500.00")
    june = next(row for row in hannah if row.period == "2026-06")
    assert june.net_expense == Decimal("0.00")
    assert june.cumulative_expense == Decimal("0.00")

    priya = [row for row in rows if row.grant_number == "GR-US-003"]
    assert priya[0].period == "2024-03"
    march = next(row for row in priya if row.period == "2026-03")
    assert march.status == "forfeited"
    assert march.service_month > 12
    assert march.monthly_expense == Decimal("1000.00")
    assert next(row for row in priya if row.period == "2026-06").net_expense == Decimal("0.00")


def test_the_subledger_knows_only_what_hr_had_recorded(client):
    """As of March, HR still showed Priya active (it recorded her exit on 2 April)."""
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    march_view = next(row for row in _schedule("2026-03") if row.grant_number == "GR-US-003" and row.period == "2026-03")
    assert march_view.status == "vesting"
    june_view = next(row for row in _schedule("2026-06") if row.grant_number == "GR-US-003" and row.period == "2026-03")
    assert june_view.status == "forfeited"


def test_an_hr_fact_loaded_later_changes_the_subledger(client):
    """A termination that exists only in a loaded July HR file, not in the generator's cast, stops Riley's expense."""
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200

    def riley_leaves(drop) -> None:
        row = next(item for item in drop.hr if item["employee_number"] == "NL-US-005")
        row.update(status="terminated", termination_date="2026-07-10", termination_recorded_on="2026-07-11")

    july = build_drop("2026-07", plants=[Plant("riley_leaves", "sources", riley_leaves)], closed=False)
    assert client.post("/world/drops", headers=CONTROLLER, json=july).status_code == 200
    riley = next(row for row in _schedule("2026-07") if row.grant_number == "GR-US-005" and row.period == "2026-07")
    assert riley.status == "forfeited"


def test_reading_schedules_writes_nothing(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    assert client.get("/equity/schedules?period=2026-06", headers=AGENT).status_code == 200
    assert client.get("/equity/schedules?period=2026-06", headers=STRANGER).status_code == 403
    with SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(EquityScheduleRun)) == 0
        assert session.scalar(select(func.count()).select_from(EquityGrantMonth)) == 0


def test_june_schedules(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    rows = client.get("/equity/schedules?period=2026-06", headers=AGENT).json()["schedules"]
    assert "GR-US-006-DUP" not in {row["grant_number"] for row in rows}
    assert _named(rows, "Priya Shah", "2026-06")["status"] == "forfeited"
    lukas = _named(rows, "Lukas Weber", "2026-06")
    assert (lukas["board_approved"], lukas["status"], lukas["net_expense"]) == (False, "pending_approval", "0.00")
    tomas = _named(rows, "Tomas Fischer", "2026-06")
    assert (tomas["entity_code"], tomas["currency"], tomas["booking_currency"]) == ("UK", "EUR", "GBP")
    assert tomas["net_expense"] == "500.00"
    assert tomas["fx_rate"] == "0.850980"
    assert tomas["booked_net_expense"] == "425.49"
    riley = _named(rows, "Riley Nguyen", "2026-06")
    assert (riley["status"], riley["net_expense"], riley["entity_code"]) == ("vesting", "750.00", "US")
    jonah = _named(rows, "Jonah Hale", "2026-06")
    assert (jonah["vest_end"], jonah["net_expense"]) == ("2028-03-01", "500.00")


def test_june_drafts_a_true_up_not_a_second_booking(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    locked = client.post("/equity/drafts?period=2026-03", headers=AGENT)
    assert locked.status_code == 409
    assert locked.json()["error"] == "period_locked"
    assert client.post("/equity/drafts?period=2026-06", headers=CONTROLLER).status_code == 403

    response = client.post("/equity/drafts?period=2026-06", headers=AGENT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window_start"] == "2026-01"
    assert all(entry["balanced"] and entry["status"] == "draft" for entry in body["journals"])

    us = _journal(body["journals"], "US stock compensation true-up")
    assert us["booking_key"] == "equity:trueup:US:2026-06"
    assert _line(us, "5200", "Product")["credit"] == "3500.00"
    assert {line["department"] for line in us["lines"] if line["account_code"] == "5200"} == {"Product"}
    uk = _journal(body["journals"], "UK stock compensation true-up")
    assert _line(uk, "5200", "G&A")["credit"] == "5500.00"
    assert _line(uk, "5200", "Engineering")["debit"] == "425.49"
    de = _journal(body["journals"], "DE stock compensation true-up")
    assert _line(de, "5200", "Engineering")["credit"] == "6500.00"

    breakdown = {(row["grant_number"], row["entity_code"]): row for row in body["trueup"]}
    assert breakdown[("GR-US-003", "US")]["difference"] == "-3000.00"
    assert breakdown[("GR-US-003", "US")]["reason"] == "forfeiture_still_expensed"
    assert breakdown[("GR-US-003", "US")]["ask"] == "dana.ruiz"
    assert breakdown[("GR-UK-003", "UK")]["difference"] == "-5500.00"
    assert breakdown[("GR-DE-001", "DE")]["difference"] == "-6000.00"
    assert breakdown[("GR-DE-001", "DE")]["reason"] == "missing_grant_approval"
    assert breakdown[("GR-US-006-DUP", "US")]["difference"] == "-500.00"
    assert breakdown[("GR-US-006-DUP", "US")]["reason"] == "platform_grant_without_signed_grant"
    assert breakdown[("GR-DE-004", "DE")]["reason"] == "expense_in_wrong_entity"
    assert breakdown[("GR-DE-004", "UK")]["difference"] == "425.49"
    assert ("GR-US-005", "US") not in breakdown

    kinds = {(note["kind"], note["legal_name"]) for note in body["notes"]}
    assert ("missing_grant_approval", "Lukas Weber") in kinds
    assert ("vest_end_mismatch", "Jonah Hale") in kinds
    assert ("platform_grant_without_signed_grant", "Nadia Rossi") in kinds


def test_after_the_true_up_the_books_match_the_subledger(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    body = client.post("/equity/drafts?period=2026-06", headers=AGENT).json()
    for entry in body["journals"]:
        assert client.post(f"/journal-entries/{entry['id']}/approve", headers=CONTROLLER).status_code == 200
        assert client.post(f"/journal-entries/{entry['id']}/post", headers=CONTROLLER).status_code == 200

    earned: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for row in _schedule("2026-06"):
        if "2026-01" <= row.period <= "2026-06":
            earned[(row.entity_code, row.department)] += row.booked_net_expense
    for entity in ("US", "UK", "DE"):
        booked = _booked_5200(client, entity, JANUARY_TO_JUNE)
        for department in set(booked) | {dept for (code, dept) in earned if code == entity}:
            assert booked.get(department, Decimal("0")) == earned.get((entity, department), Decimal("0")), (
                entity,
                department,
            )

    again = client.post("/equity/drafts?period=2026-06", headers=AGENT).json()
    assert again["journals"] == []


def test_july_does_not_repeat_junes_corrections(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    june = client.post("/equity/drafts?period=2026-06", headers=AGENT).json()
    for entry in june["journals"]:
        client.post(f"/journal-entries/{entry['id']}/approve", headers=CONTROLLER)
        client.post(f"/journal-entries/{entry['id']}/post", headers=CONTROLLER)
    july_drop = build_drop("2026-07", plants=[], closed=False)
    assert client.post("/world/drops", headers=CONTROLLER, json=july_drop).status_code == 200

    july = client.post("/equity/drafts?period=2026-07", headers=AGENT).json()
    grants = {row["grant_number"] for row in july["trueup"]}
    # July's platform still expenses Lukas without a board approval. That is July's own error.
    assert grants == {"GR-DE-001"}
    assert next(row for row in july["trueup"])["difference"] == "-1000.00"


def test_a_rerun_withdraws_the_old_draft(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    first = client.post("/equity/drafts?period=2026-06", headers=AGENT).json()
    second = client.post("/equity/drafts?period=2026-06", headers=AGENT).json()
    us_entries = [
        entry
        for entry in client.get("/entities/US/journal-entries?period=2026-06", headers=CONTROLLER).json()
        if entry["booking_key"] == "equity:trueup:US:2026-06"
    ]
    assert sorted(entry["status"] for entry in us_entries) == ["draft", "withdrawn"]
    first_us = _journal(first["journals"], "US stock")
    second_us = _journal(second["journals"], "US stock")
    assert first_us["number"] != second_us["number"]
    assert second["schedule_run"] != first["schedule_run"]


def test_reconcile_uses_the_subledger_not_a_department_shortcut(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    june = client.post("/reconciliations?period=2026-06", headers=AGENT)
    assert june.status_code == 200, june.text
    priya = next(row for row in june.json()["findings"] if row["subject_name"] == "Priya Shah")
    ledger = priya["ranking"][3]["claim"]
    assert "5200" in ledger
    assert "GR-US-003" in ledger
    assert "0.00" in ledger
    equity = priya["ranking"][2]["claim"]
    assert "vesting" in equity
    assert "forfeited" in equity
