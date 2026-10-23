"""Task 17: Vestly's memory of the company, the patterns it learns, and the history detectors that use them."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.agent.execute import AgentContext, execute
from app.agent.loop import run_case
from app.agent.model import ScriptedModel
from app.auth.identities import Actor
from app.cases.close import run_close
from app.cases.models import Case
from app.controls.callbacks import check_pay_destination, record_callback
from app.db import SessionLocal
from app.ledger.models import JournalEntry
from app.memory.history import department_baseline, employee_timeline, learn
from app.memory.models import Observation
from app.record.models import EvidenceEvent
from app.world.drops import company_people
from app.world.loader import load_world

CONTROLLER = Actor("dana.ruiz", "controller")
PAYROLL = Actor("vestly.payroll", "agent")


@pytest.fixture()
def company(client):
    with SessionLocal() as db:
        load_world(db, CONTROLLER, "company")
        yield db


@pytest.fixture()
def closed(company):
    run_close(company, "2026-10")
    return company


def _case(db, key: str) -> Case:
    return db.scalar(select(Case).where(Case.key == key))


# ---------------------------------------------------------------- the memory


def test_a_tenured_employee_has_more_than_four_years_of_memory_including_their_2022_bank_account(company):
    mover = next(person for person in company_people() if person.get("transfers") and person["employee_number"] != "NL-DE-004")
    timeline = employee_timeline(company, mover["employee_number"], "2026-10")
    assert timeline["months_in_history"] == 58 and timeline["years_of_history"] >= 4
    assert timeline["history_from"] == "2022-01" and timeline["history_to"] == "2026-10"
    accounts = timeline["bank_accounts"]
    assert accounts[0] == {"value": mover["bank_account"], "from": "2022-01", "to": accounts[0]["to"]}
    assert accounts[-1]["value"] == mover["transfers"][0]["bank_account"]
    assert [span["value"].split()[0] for span in timeline["entity_and_department"]] == [mover["entity_code"], mover["transfers"][0]["entity_code"]]
    assert len(timeline["pay_received"]) == 58


def test_the_timeline_shows_approved_and_unapproved_pay(company):
    chloe = employee_timeline(company, "NL-US-244", "2026-10")
    assert [item["monthly_rate"] for item in chloe["approved_pay_changes"]] == ["9450.00"]
    assert [span["value"] for span in chloe["hr_monthly_salary"]] == ["9000.00", "9450.00", "10400.00"]
    assert chloe["hr_monthly_salary"][-1]["from"] == "2025-03"
    assert employee_timeline(company, "NL-XX-000", "2026-10") is None


def test_a_department_baseline_rests_on_four_years_and_is_recorded_once(company):
    baseline = department_baseline(company, "US", "Engineering", "2026-10")
    assert len(baseline["months"]) == 48 and baseline["months"][0] == "2022-11" and baseline["months"][-1] == "2026-10"
    assert baseline["employer_contribution_rate"] == "0.0400"
    assert Decimal(baseline["monthly_gross_band"]["low"]) < Decimal(baseline["monthly_gross_band"]["median"]) < Decimal(baseline["monthly_gross_band"]["high"])
    learned = learn(company, "2026-10")
    assert len(learned) == 12  # three entities, four departments
    row = next(item for item in learned if item.subject == "US:Engineering")
    assert json.loads(row.months_json) == baseline["months"]
    event = company.get(EvidenceEvent, row.evidence_id)
    assert event.action == "pattern_learned" and json.loads(event.payload_json)["months"] == baseline["months"]
    again = learn(company, "2026-10")
    assert {item.id for item in again} == {item.id for item in learned}
    assert company.scalar(select(Observation).where(Observation.subject == "US:Engineering").offset(1).limit(1)) is None


# ---------------------------------------------------------------- the agent's memory tools


def test_the_agent_reads_one_persons_history_only_inside_their_case(closed):
    case = _case(closed, "salary_without_approval:NL-US-244:2026-10")
    ctx = AgentContext(closed, case, PAYROLL, "run-memory")
    mine = execute(ctx, "get_employee_history", {"employee_number": "NL-US-244"})
    assert mine.status == "ok" and mine.data["approved_pay_changes"][0]["monthly_rate"] == "9450.00"
    assert mine.establishes == (("NL-US-244", "found"),)
    other = execute(ctx, "get_employee_history", {"employee_number": "NL-US-241"})
    assert other.status == "refused" and other.error.startswith("PRV-1")


def test_the_agent_sees_department_totals_and_the_learned_observation(closed):
    case = _case(closed, "department_mismatch:NL-US-245:2026-10")
    ctx = AgentContext(closed, case, PAYROLL, "run-department")
    result = execute(ctx, "get_department_history", {"entity_code": "US", "department": "GTM"})
    assert result.status == "ok"
    assert result.data["observation"]["subject"] == "US:GTM" and result.data["observation"]["evidence_id"].startswith("ev_")
    assert len(result.data["months"]) == 48
    assert "legal_name" not in json.dumps(result.data) and "employee_number" not in json.dumps(result.data)


def test_the_history_cases_are_worked_by_the_agent_and_reported_honestly(closed):
    for key in (
        "salary_without_approval:NL-US-244:2026-10",
        "department_mismatch:NL-US-245:2026-10",
        "bank_account_reused:NL-US-243:2026-10",
        "missing_from_register:NL-US-246:2026-10",
    ):
        run = run_case(closed, _case(closed, key), ScriptedModel())
        assert run.status == "reported", (key, run.problems)
        assert "get_employee_history" in [item["tool"] for item in run.tool_statuses]


# ---------------------------------------------------------------- what the history changes in the books and the gate


def test_wages_follow_hrs_department_and_a_closed_month_shortfall_is_caught_up_once(closed):
    wages = closed.scalar(select(JournalEntry).where(JournalEntry.booking_key == "payroll:wages:US:2026-10"))
    by_department = {}
    for line in wages.lines:
        if line.account.code == "5000":
            sources = json.loads(line.sources_json)
            by_department[line.department] = {item["id"] for item in sources}
    from app.world.models import PayrollRegisterRow

    diego = closed.scalar(select(PayrollRegisterRow).where(PayrollRegisterRow.employee_number == "NL-US-245", PayrollRegisterRow.period == "2026-10"))
    assert diego.department == "Engineering"  # the register's coding
    assert diego.id in by_department["GTM"] and diego.id not in by_department["Engineering"]

    catchup = closed.scalar(select(JournalEntry).where(JournalEntry.booking_key == "payroll:contribution_catchup:NL-UK-186:2026-07"))
    assert catchup is not None and catchup.status == "draft"
    assert sum(line.debit for line in catchup.lines) == Decimal("144.00")
    from app.controls.approvals import decide_entry
    from app.controls.posting import post_entry
    from app.payroll.service import draft_contribution_catchups, draft_payroll

    # A re-run of the close replaces its own drafts; there is never more than one live entry for the shortfall.
    draft_payroll(closed, "2026-10", PAYROLL)
    draft_contribution_catchups(closed, "2026-10", PAYROLL)
    live = closed.scalars(select(JournalEntry).where(JournalEntry.booking_key == "payroll:contribution_catchup:NL-UK-186:2026-07", JournalEntry.status.in_(("draft", "approved", "posted")))).all()
    assert len(live) == 1
    # Once it is approved and posted, no later run drafts it again: it is reported as already on file.
    decide_entry(closed, live[0].id, Actor("chris.lam", "finance_mgr"), approve=True, note="Pension shortfall confirmed.")
    # Without the 31 October rate its USD size cannot be measured, so the stricter path applies: the controller too.
    assert closed.get(JournalEntry, live[0].id).status == "draft"
    decide_entry(closed, live[0].id, CONTROLLER, approve=True, note="Approved.")
    post_entry(closed, live[0].id, CONTROLLER)
    again = draft_contribution_catchups(closed, "2026-10", PAYROLL)
    assert not any(entry.booking_key == "payroll:contribution_catchup:NL-UK-186:2026-07" for entry in again["journals"])
    assert any(item["booking_key"] == "payroll:contribution_catchup:NL-UK-186:2026-07" and item["status"] == "posted" for item in again["already_on_file"])


def test_an_account_another_person_had_needs_a_call_back_before_anyone_is_paid_to_it(company):
    verdict = check_pay_destination(company, "NL-US-243", "1004002420", "2026-10", "2026-10-30")
    assert verdict.allowed is False and verdict.rule == "BNK-1"
    assert "Marcus Bell (NL-US-242)" in verdict.reason
    record_callback(company, Actor("lee.park", "hr_ops"), "NL-US-243", "1004002420", "confirmed", "2026-10-28", "Elena confirmed her own account.")
    assert check_pay_destination(company, "NL-US-243", "1004002420", "2026-10", "2026-10-30").allowed is True
    # An account only one person ever had is unaffected.
    assert check_pay_destination(company, "NL-US-001", "1002003001", "2026-10", "2026-10-30").allowed is True
