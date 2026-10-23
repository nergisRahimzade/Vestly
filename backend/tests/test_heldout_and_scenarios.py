"""Task 20: November and December held out, scenarios a judge can ask for, and documents that arrive as PDFs."""

import json

import pytest
from sqlalchemy import select

from app.agent.execute import AgentContext, execute
from app.auth.identities import Actor
from app.cases.close import run_close
from app.cases.models import Case, CaseQuestion
from app.controls.clock import advance, today
from app.db import SessionLocal
from app.eval.score import score_period
from app.ledger.errors import LedgerError
from app.world.drops import HELDOUT_KEY_PATH, build_company_drop, build_heldout_key
from app.world.loader import load_world, receive_month
from app.world.models import SourceDocument
from app.world.pdf import decode, pdf_text, render_pdf, scan_noise

CONTROLLER = Actor("dana.ruiz", "controller")


def _days_until(db, iso: str) -> int:
    from datetime import date

    return (date.fromisoformat(iso) - date.fromisoformat(today(db))).days


# ---------------------------------------------------------------- scenarios


def test_an_injected_ghost_employee_is_caught_and_routed_to_hr(client):
    with SessionLocal() as db:
        loaded = load_world(db, CONTROLLER, "cast", [{"kind": "ghost_employee", "employee_number": "NL-US-999", "period": "2026-06"}])
        assert loaded["scenarios"] == [{"kind": "ghost_employee", "employee_number": "NL-US-999", "period": "2026-06"}]
        run_close(db, "2026-06")
        case = db.scalar(select(Case).where(Case.kind == "unconfirmed_person", Case.subject_number == "NL-US-999"))
        assert case is not None and case.outcome == "held"
        asked = db.scalars(select(CaseQuestion.asked_to).where(CaseQuestion.case_id == case.id)).all()
        assert asked == ["lee.park"]
        # The shipped June ghost is still caught next to the injected one.
        assert db.scalar(select(Case).where(Case.kind == "unconfirmed_person", Case.subject_number == "PAY-0099")) is not None


def test_a_world_with_scenarios_is_not_kept_as_the_plain_worlds_copy(client):
    from app.db import ENGINE, Base, init_db

    with SessionLocal() as db:
        load_world(db, CONTROLLER, "cast", [{"kind": "wrong_contribution", "employee_number": "NL-US-002", "period": "2026-06"}])
    Base.metadata.drop_all(ENGINE)
    init_db()
    with SessionLocal() as db:
        load_world(db, CONTROLLER, "cast")
        run_close(db, "2026-06")
        assert db.scalar(select(Case).where(Case.kind == "contribution_rate_break", Case.subject_number == "NL-US-002")) is None


@pytest.mark.parametrize(
    "scenario, message",
    [
        ({"kind": "ghost_employee", "employee_number": "NL-US-001", "period": "2026-06"}, "is a real employee"),
        ({"kind": "teleport", "employee_number": "NL-US-001", "period": "2026-06"}, "Unknown scenario kind"),
        ({"kind": "duplicate_payment", "employee_number": "NL-US-001", "period": "2027-01"}, "outside it"),
        ({"kind": "vesting_typo", "employee_number": "NL-ZZ-404", "period": "2026-06"}, "No employee"),
        ({"kind": "duplicate_payment", "employee_number": "NL-US-001"}, "KIND"),
    ],
    ids=["ghost that exists", "unknown kind", "month outside the world", "unknown person", "missing month"],
)
def test_a_scenario_that_cannot_happen_is_refused_before_anything_loads(client, scenario, message):
    with SessionLocal() as db:
        with pytest.raises(LedgerError) as refused:
            load_world(db, CONTROLLER, "cast", [scenario])
        assert refused.value.code == "invalid_scenario" and (message in refused.value.message or "kind, employee_number, period" in refused.value.message)
        assert db.scalar(select(SourceDocument).limit(1)) is None


def test_the_reset_script_keeps_the_old_database_and_loads_the_scenario(tmp_path):
    from app.world.reset import reset

    db_path = tmp_path / "demo.db"
    db_path.write_bytes(b"an earlier run")
    result = reset(db_path, "cast", [{"kind": "duplicate_payment", "employee_number": "NL-US-002", "period": "2026-06"}], runs=tmp_path / "runs")
    kept = list((tmp_path / "runs").iterdir())
    assert len(kept) == 1 and kept[0].read_bytes() == b"an earlier run"
    assert result["previous_kept_at"] == str(kept[0]) and result["people"] > 0
    assert db_path.stat().st_size > 10_000


# ---------------------------------------------------------------- held-out months


def test_a_held_out_month_arrives_only_on_its_day_and_in_order(client):
    with SessionLocal() as db:
        load_world(db, CONTROLLER, "company")
        assert today(db) == "2026-11-02"
        for period, code in (("2026-12", "month_out_of_order"), ("2026-11", "not_arrived"), ("2026-10", "not_held_out")):
            with pytest.raises(LedgerError) as refused:
                receive_month(db, period, CONTROLLER)
            assert refused.value.code == code
        with pytest.raises(LedgerError) as refused:
            receive_month(db, "2026-11", Actor("lee.park", "hr_ops"))
        assert refused.value.rule == "ACC-1"
        advance(db, CONTROLLER, _days_until(db, "2026-12-02"))
        assert receive_month(db, "2026-11", CONTROLLER)["status"] == "loaded"
        # November's documents: the scanned email is kept as a PDF, its text beside it names the PDF's hash.
        pdf = db.scalar(select(SourceDocument).where(SourceDocument.system == "pdf", SourceDocument.period == "2026-11"))
        text = db.scalar(select(SourceDocument).where(SourceDocument.system == "email", SourceDocument.period == "2026-11"))
        assert pdf.body.startswith("data:application/pdf;base64,")
        assert f"text read from PDF sha256:{pdf.content_hash}" in text.title
        assert "New account: 9922003344" in text.body and text.body == pdf_text(decode(pdf.body))


def test_the_bank_change_from_a_scanned_pdf_is_held_and_vestly_reads_the_scan_itself(client):
    with SessionLocal() as db:
        load_world(db, CONTROLLER, "company")
        advance(db, CONTROLLER, _days_until(db, "2026-12-02"))
        receive_month(db, "2026-11", CONTROLLER)
        run_close(db, "2026-11")
        case = db.scalar(select(Case).where(Case.kind == "bank_detail_change", Case.subject_number == "NL-UK-102"))
        assert case.outcome == "held" and case.reason_code == "bank_change_unverified"
        from app.reconcile.models import Reconciliation

        basis = json.loads(db.get(Reconciliation, case.finding_id).basis_json)
        assert {"type": "rule", "id": "BNK-1"} in basis
        assert not any(item["type"] == "precedent" for item in basis), "the March ruling is scoped to the US"
        pdf = db.scalar(select(SourceDocument).where(SourceDocument.system == "pdf", SourceDocument.period == "2026-11"))
        ctx = AgentContext(db, case, Actor("vestly.payroll", "agent"), "run-pdf")
        result = execute(ctx, "extract_document", {"content_hash": pdf.content_hash})
        assert result.status == "ok", result.error
        account = next(field for field in result.data["fields"] if field["name"] == "requested_account")
        assert account["value"] == "9922003344" and account["quote"] == "New account: 9922003344"


def test_the_committed_held_out_key_is_the_one_the_generator_writes():
    assert json.loads(HELDOUT_KEY_PATH.read_text(encoding="utf-8")) == build_heldout_key()


def test_held_out_plants_leave_october_and_earlier_untouched():
    november = build_company_drop("2026-11")
    assert any(name.startswith("scanned_email:") for name in november["plants"])
    assert not any(name.startswith("scanned_email:") for name in build_company_drop("2026-10")["plants"])


def test_november_and_december_score_with_no_false_holds_on_the_full_company():
    for period in ("2026-11", "2026-12"):
        report, _runs = score_period(period, runs=1, world="company")
        metrics = report["metrics"]
        assert metrics["caught"]["value"] == metrics["caught"]["of"], [case for case in report["cases"] if not case["caught"]]
        assert metrics["false_holds"]["value"] / metrics["false_holds"]["of"] < 0.05
        assert metrics["money_correct"]["met"] and metrics["never"]["met"] and metrics["evidence_complete"]["met"]


# ---------------------------------------------------------------- PDFs


def test_a_scan_misreads_letters_but_keeps_digits_addresses_and_labels():
    lines = ["From: oscar.zimmer@northlight.example", "New account: 9922003344", "Hello payroll, please change my salary account."]
    noisy = scan_noise(lines, seed=11, rate=1.0)
    assert noisy[0] == lines[0] and noisy[1].startswith("New account: ") and "9922003344" in noisy[1]
    assert noisy[2] != lines[2]
    assert render_pdf("t", noisy) == render_pdf("t", noisy), "the same scan gives the same file and the same hash"
    assert pdf_text(render_pdf("t", noisy)).splitlines() == noisy


@pytest.mark.parametrize(
    "kind, opens, subject, outcome, owner",
    [
        ("bank_change_by_email", "bank_detail_change", "NL-UK-002", "held", "lee.park"),
        ("wrong_contribution", "contribution_rate_break", "NL-UK-002", "ready", "payroll.provider"),
        ("duplicate_payment", "duplicate_payment", "PAY-2026-06-NL-UK-002-2", "held", "alex.moreau"),
        ("department_miscoded", "department_mismatch", "NL-UK-002", "ready", None),
        ("missing_from_register", "missing_from_register", "NL-UK-002", "held", "payroll.provider"),
        ("vesting_typo", "vest_schedule_mismatch", "NL-UK-002", "ready", "morgan.li"),
        ("duplicate_grant", "duplicate_grant", "NL-UK-002", "rejected_duplicate", "morgan.li"),
    ],
)
def test_every_scenario_kind_planted_on_a_clean_person_opens_its_case(client, kind, opens, subject, outcome, owner):
    """Owen Blake is clean in the cast's June; each scenario on him must open the case its kind calls for."""
    with SessionLocal() as db:
        load_world(db, CONTROLLER, "cast", [{"kind": kind, "employee_number": "NL-UK-002", "period": "2026-06"}])
        run_close(db, "2026-06")
        case = db.scalar(select(Case).where(Case.kind == opens, Case.subject_number == subject))
        assert case is not None, [(row.kind, row.subject_number) for row in db.scalars(select(Case)) if "NL-UK-002" in row.subject_number]
        assert case.outcome == outcome
        asked = db.scalars(select(CaseQuestion.asked_to).where(CaseQuestion.case_id == case.id)).all()
        assert asked and (owner is None or owner in asked)
