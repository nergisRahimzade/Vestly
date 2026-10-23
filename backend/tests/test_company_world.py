"""The company world (D21, D22): about 300 people, every department, four years of history, aligned with today.

January 2022 to September 2026 arrive closed; October 2026 is open; amounts translate at real month-end rates.
The first load takes about half a minute; later fresh databases in the same run restore it.
"""

import json
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.auth.identities import Actor
from app.cases.close import run_close
from app.cases.models import Case
from app.db import ENGINE, Base, SessionLocal, init_db
from app.eval.score import score_period
from app.eval.scorer import load_key
from app.ledger.models import Entity, Period
from app.payroll.calc import overpaid_gross
from app.record.models import EvidenceEvent
from app.world.drops import (
    COMPANY_OPEN,
    build_company_drop,
    company_manifest,
    company_manifest_path,
    company_people,
    company_periods,
)
from app.world.loader import load_world

CONTROLLER = Actor("dana.ruiz", "controller")
CORE = {f"A{number:02d}" for number in range(1, 14)}
CAUGHT_FROM_HISTORY_NOW = {"H01", "H08"}  # the existing detectors scan every loaded month
NEEDS_HISTORY_DETECTORS = {"H02", "H03", "H04", "H05", "H06", "H07"}  # task 17


def _months(first: str, last: str) -> list[str]:
    return [period for period in company_periods() if first <= period <= last]


# ---------------------------------------------------------------- the generated company


def test_the_company_has_every_department_in_every_entity_and_four_years_of_history():
    people = company_people()
    assert len(people) >= 300
    for entity in ("US", "UK", "DE"):
        assert {person["department"] for person in people if person["entity_code"] == entity} == {"Engineering", "Product", "GTM", "G&A"}
    tenured = [
        person for person in people
        if person["hire_date"] <= "2022-10-31" and person.get("termination_date", "9999") >= "2026-10-01"
    ]
    assert len(tenured) >= 150
    numbers = {person["employee_number"] for person in tenured}
    for period in _months("2022-11", COMPANY_OPEN):
        in_hr = {row["employee_number"] for row in build_company_drop(period)["hr"]}
        assert numbers <= in_hr, f"{period} is missing people with four years of history"


def test_every_month_from_2022_balances_and_closes_at_its_real_month_end_rate():
    periods = company_periods()
    assert periods[0] == "2022-01" and periods[-1] == "2026-10" and len(periods) == 58
    for period in periods:
        drop = build_company_drop(period)
        assert drop["close_period"] is (period < COMPANY_OPEN)
        for entry in drop["ledger_entries"]:
            debits = sum(Decimal(line["debit"]) for line in entry["lines"])
            credits = sum(Decimal(line["credit"]) for line in entry["lines"])
            assert debits == credits > 0, (period, entry["memo"])
        assert ("gbp_usd_reference" in drop["rate_card"]) is (period < COMPANY_OPEN), period


def test_the_generator_matches_the_committed_manifest():
    assert company_manifest() == json.loads(company_manifest_path().read_text(encoding="utf-8"))


def test_the_company_answer_key_names_real_people_and_payment_lines():
    key = load_key(world="company")
    people = {person["employee_number"] for person in company_people()} | {"PAY-0099"}
    payments = {row["payment_id"] for period in ("2026-08", "2026-10") for row in build_company_drop(period)["funding"]}
    for row in key["anomalies"]:
        assert row["scored_in"] == "2026-10"
        for subject in row["subjects"] + row.get("related", []):
            assert subject in people or subject in payments, (row["id"], subject)
    assert {row["id"] for row in key["anomalies"]} == CORE | CAUGHT_FROM_HISTORY_NOW | NEEDS_HISTORY_DETECTORS


def test_a_leaver_from_years_ago_is_overpaid_one_pay_period_not_years():
    """The overpayment counts only the days of the pay period after the last day (found by the company's history)."""
    assert overpaid_gross(Decimal("9800.00"), "2026-07-01", "2026-07-31", "2023-05-19") == Decimal("9800.00")
    assert overpaid_gross(Decimal("3100.00"), "2026-07-01", "2026-07-31", "2026-07-15") == Decimal("1600.00")
    assert overpaid_gross(Decimal("3100.00"), "2026-07-01", "2026-07-31", "2026-07-31") == Decimal("0.00")


# ---------------------------------------------------------------- loaded


def test_loading_the_company_aligns_it_with_today_and_a_fresh_copy_restores_it(client):
    with SessionLocal() as db:
        result = load_world(db, CONTROLLER, "company")
        assert result["world"] == "company" and result["people"] >= 300
        statuses = dict(
            db.execute(
                select(func.printf("%04d-%02d", Period.year, Period.month), func.group_concat(Period.status.distinct()))
                .join(Entity, Entity.id == Period.entity_id)
                .group_by(Period.year, Period.month)
            ).all()
        )
        assert all(statuses[period] == "locked" for period in _months("2022-01", "2026-09"))
        assert statuses["2026-10"] == "open" and "2026-11" not in statuses
        from app.controls.clock import today

        assert today(db) == "2026-11-02"
        head = db.scalar(select(EvidenceEvent.hash).order_by(EvidenceEvent.seq.desc()).limit(1))
        count = db.scalar(select(func.count()).select_from(EvidenceEvent))
    Base.metadata.drop_all(ENGINE)
    init_db()
    with SessionLocal() as db:
        again = load_world(db, CONTROLLER, "company")
        assert again.get("restored") is True
        assert db.scalar(select(EvidenceEvent.hash).order_by(EvidenceEvent.seq.desc()).limit(1)) == head
        assert db.scalar(select(func.count()).select_from(EvidenceEvent)) == count
    assert client.get("/world/summary").json()["world"] == "company"


def test_the_october_close_waits_for_the_october_rate_and_opens_the_cases(client):
    with SessionLocal() as db:
        load_world(db, CONTROLLER, "company")
        summary = run_close(db, "2026-10")
        assert summary["waiting"] and summary["waiting"][0]["step"] == "equity_trueup"
        assert "2026-10" in summary["waiting"][0]["reason"]
        keys = {row.kind + ":" + row.subject_number for row in db.scalars(select(Case).where(Case.period == "2026-10"))}
    key = load_key(world="company")
    for row in key["anomalies"]:
        if row["id"] in CORE - {"A13"} | CAUGHT_FROM_HISTORY_NOW:
            for subject in row["subjects"]:
                assert f"{row['kind']}:{subject}" in keys, row["id"]



def test_the_company_october_close_scores_three_times_with_no_false_holds():
    report, results = score_period("2026-10", runs=3, world="company")
    cases = {row["id"]: row for row in report["cases"]}
    assert {case_id for case_id, row in cases.items() if row["caught"]} == CORE | CAUGHT_FROM_HISTORY_NOW
    for case_id in NEEDS_HISTORY_DETECTORS:
        assert all("no single" in problem for problem in cases[case_id]["problems"]), cases[case_id]
    metrics = report["metrics"]
    assert metrics["false_holds"]["value"] == 0 and metrics["false_holds"]["of"] > 500
    assert metrics["evidence_complete"]["rate"] == 1.0
    assert metrics["money_correct"]["rate"] == 1.0
    assert metrics["never"]["value"] == 0
    assert len(results) == 3
