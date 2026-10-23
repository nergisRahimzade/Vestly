import hashlib
import json
from decimal import Decimal

from app.world.drops import build_drop, canonical, fixture_dir
from helpers import AGENT, CONTROLLER, STRANGER



def _by_name(rows: list[dict], name: str) -> dict:
    return next(row for row in rows if row["legal_name"] == name)


def test_committed_file_drops_match_the_generator():
    for period in ("2026-03", "2026-06"):
        path = fixture_dir() / f"{period}.json"
        assert path.read_text(encoding="utf-8") == canonical(build_drop(period))


def test_company_file_drops(client):
    loaded = client.post("/world/load", headers=CONTROLLER)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json() == {"status": "loaded", "people": 15}

    summary = client.get("/world/summary")
    assert summary.json()["company"] == "Northlight Labs"
    assert summary.json()["loaded"] is True

    departments = {row["name"] for row in client.get("/world/departments").json()}
    assert departments == {"Engineering", "Product", "GTM", "G&A"}

    people = client.get("/world/people", headers=CONTROLLER).json()
    assert len(people) == 15
    assert _by_name(people, "Sam Ortiz")["listed_in_hr"] is False
    assert all(row["listed_in_hr"] for row in people if row["legal_name"] != "Sam Ortiz")

    march_hr = client.get("/world/hr?period=2026-03", headers=CONTROLLER).json()
    june_hr = client.get("/world/hr?period=2026-06", headers=CONTROLLER).json()
    assert "Sam Ortiz" not in {row["legal_name"] for row in june_hr}
    priya_march = _by_name(march_hr, "Priya Shah")
    priya_june = _by_name(june_hr, "Priya Shah")
    assert priya_march["status"] == "active"
    assert priya_june["termination_date"] == "2026-03-12"
    assert priya_june["termination_recorded_on"] == "2026-04-02"
    maya = _by_name(june_hr, "Maya Chen")
    assert maya["bank_account"] == "1002003001"

    march_pay = client.get("/world/payroll?period=2026-03", headers=CONTROLLER).json()
    june_pay = client.get("/world/payroll?period=2026-06", headers=CONTROLLER).json()
    assert _by_name(march_pay, "Priya Shah")["pay_period_end"] == "2026-03-15"
    assert "Priya Shah" not in {row["legal_name"] for row in june_pay}
    assert _by_name(june_pay, "Sam Ortiz")["employee_number"] == "PAY-0099"
    evan = _by_name(june_pay, "Evan Brooks")
    assert evan["retro_effective_date"] == "2026-04-01"
    assert evan["retro_gross"] == "4000.00"
    amira = _by_name(june_pay, "Amira Khan")
    assert Decimal(amira["employer_contribution"]) / Decimal(amira["gross_wages"]) == Decimal("0.03")
    riley = _by_name(june_pay, "Riley Nguyen")
    owen = _by_name(june_pay, "Owen Blake")
    sofia = _by_name(june_pay, "Sofia Keller")
    greta = _by_name(june_pay, "Greta Hoffmann")
    assert Decimal(riley["employer_contribution"]) / Decimal(riley["gross_wages"]) == Decimal("0.04")
    assert Decimal(owen["employer_contribution"]) / Decimal(owen["gross_wages"]) == Decimal("0.05")
    assert Decimal(sofia["employer_contribution"]) / Decimal(sofia["gross_wages"]) == Decimal("0.20")
    assert Decimal(greta["employer_contribution"]) / Decimal(greta["gross_wages"]) == Decimal("0.20")

    june_equity = client.get("/world/equity?period=2026-06", headers=CONTROLLER).json()
    priya_grant = _by_name(june_equity, "Priya Shah")
    hannah_grant = _by_name(june_equity, "Hannah Price")
    lukas_grant = _by_name(june_equity, "Lukas Weber")
    assert priya_grant["platform_status"] == "vesting"
    assert priya_grant["monthly_expense"] == "1000.00"
    assert hannah_grant["monthly_expense"] == "500.00"
    assert _by_name(june_hr, "Hannah Price")["termination_date"] < hannah_grant["cliff_end"]
    assert lukas_grant["board_approved"] is False
    nadia_grants = [row for row in june_equity if row["legal_name"] == "Nadia Rossi"]
    assert {row["grant_number"] for row in nadia_grants} == {"GR-US-006", "GR-US-006-DUP"}
    jonah_grant = _by_name(june_equity, "Jonah Hale")
    assert jonah_grant["vest_end"] == "2027-03-01"
    tomas_hr = _by_name(june_hr, "Tomas Fischer")
    tomas_pay = _by_name(june_pay, "Tomas Fischer")
    assert tomas_hr["entity_code"] == "UK"
    assert tomas_pay["entity_code"] == "DE"
    ines = _by_name(june_pay, "Ines Duarte")
    assert ines["pay_period_start"] == "2026-06-02"
    assert ines["pay_period_end"] == "2026-06-25"
    assert ines["gross_wages"] == "4800.00"
    amira_cycle = _by_name(june_pay, "Amira Khan")
    assert amira_cycle["pay_period_start"] == "2026-05-26"
    assert amira_cycle["pay_period_end"] == "2026-06-25"
    greta_rows = [row for row in june_hr if row["legal_name"] == "Greta Hoffmann"]
    assert len(greta_rows) == 2

    funding = client.get("/world/funding?period=2026-06", headers=CONTROLLER).json()
    bank = client.get("/world/bank?period=2026-06", headers=CONTROLLER).json()
    sofia_bank = next(row for row in bank if row["payment_id"] == "PAY-2026-06-NL-DE-002")
    assert sofia_bank["posted_on"] == "2026-07-01"
    us_funding = [row for row in funding if row["entity_code"] == "US"]
    us_bank = [row for row in bank if row["entity_code"] == "US"]
    assert len(us_funding) == 7
    assert len(us_bank) == 4
    missing = {row["payment_id"] for row in us_funding if not row["cleared"]}
    assert missing == {
        "PAY-2026-06-NL-US-001",
        "PAY-2026-06-NL-US-002",
        "PAY-2026-06-NL-US-005",
    }
    assert {row["payment_id"] for row in us_bank} == {row["payment_id"] for row in us_funding if row["cleared"]}
    maya_payment = next(row for row in us_funding if row["legal_name"] == "Maya Chen")
    assert maya_payment["destination_account"] == "9911882277"
    assert maya_payment["cleared"] is False

    documents = client.get("/world/documents?period=2026-06", headers=CONTROLLER).json()
    maya_email = next(row for row in documents if row["title"] == "Maya Chen bank change email")
    assert "9911882277" in maya_email["body"]
    assert maya_email["content_hash"] == hashlib.sha256(maya_email["body"].encode("utf-8")).hexdigest()
    rate_card = next(row for row in documents if row["system"] == "rate_card")
    assert "5% of gross wages" in rate_card["body"]
    drop = json.loads((fixture_dir() / "2026-06.json").read_text(encoding="utf-8"))
    reference = Decimal(drop["rate_card"]["gbp_usd_reference"])
    reporting = Decimal(drop["rate_card"]["gbp_usd_reporting"])
    band = Decimal(drop["rate_card"]["fx_band"])
    assert abs(reference - reporting) / reference < band

    march_docs = client.get("/world/documents?period=2026-03", headers=CONTROLLER).json()
    rejection = next(row for row in march_docs if row["title"] == "Controller rejection of Jonah Hale bank change")
    assert "An email is not a bank-detail change." in rejection["body"]

    assert client.get("/entities/US/periods/2026-03", headers=CONTROLLER).json()["status"] == "locked"
    assert client.get("/entities/UK/periods/2026-03", headers=CONTROLLER).json()["status"] == "locked"
    assert client.get("/entities/DE/periods/2026-03", headers=CONTROLLER).json()["status"] == "locked"
    assert client.get("/entities/US/periods/2026-06", headers=CONTROLLER).json()["status"] == "open"

    june_books = client.get("/entities/US/journal-entries?period=2026-06", headers=CONTROLLER).json()
    assert june_books
    assert all("stock compensation" in entry["memo"] for entry in june_books)
    march_books = client.get("/entities/US/journal-entries?period=2026-03", headers=CONTROLLER).json()
    assert len(march_books) == 3
    assert all(entry["total_debit"] == entry["total_credit"] for entry in march_books + june_books)

    again = client.post("/world/load", headers=CONTROLLER)
    assert again.json()["status"] == "already_loaded"
    assert len(client.get("/entities/US/journal-entries?period=2026-03", headers=CONTROLLER).json()) == 3


def test_agent_reads_totals_but_cannot_load_the_world(client):
    denied = client.post("/world/load", headers=AGENT)
    assert denied.status_code == 403
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    hidden = client.get("/world/payroll?period=2026-06", headers=STRANGER)
    assert hidden.status_code == 403
    totals = client.get("/world/payroll/totals?period=2026-06", headers=AGENT)
    assert totals.status_code == 200
    assert totals.json()
