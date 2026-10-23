"""The scorer: a perfect close scores 100%, every kind of mistake costs what it should, and the real close passes three times."""

from dataclasses import replace

import pytest

from app.cases import service as case_service
from app.eval.score import main, score_period
from app.eval.scorer import Decision, RunResult, expected_cases, load_key, score

KEY = load_key()
CLEAN = ["NL-UK-002", "NL-DE-002", "NL-DE-003", "NL-UK-004", "PAY-2026-06-NL-UK-002", "PAY-2026-06-NL-DE-002"]


def perfect_run() -> RunResult:
    """What a flawless close would report, built from the key itself."""
    decisions = []
    items = list(CLEAN)
    for anomaly in KEY["anomalies"]:
        cite = anomaly.get("must_cite", {})
        for kind, subject in expected_cases(anomaly):
            amount = next((row for row in anomaly.get("expected_amounts", []) if row["subject"] == subject), None)
            decisions.append(
                Decision(
                    kind=kind,
                    subject=subject or "JE-US-2026-06-0001",
                    outcome=anomaly["expected_outcome"],
                    reason_code=anomaly["reason_code"],
                    asked=(anomaly["ask"], *anomaly.get("also_ask", [])),
                    rules=tuple(cite.get("rules", [])),
                    precedents=tuple(cite.get("precedents", [])),
                    authority_rows=(cite["authority_row"],) if cite.get("authority_row") else (),
                    amount=amount["amount"] if amount else anomaly.get("expected_amount"),
                    currency=amount["currency"] if amount else anomaly.get("expected_currency"),
                )
            )
            if subject:
                items.append(subject)
    return RunResult(period="2026-06", decisions=decisions, actions=["post:payroll:wages:US:2026-06"], items=items)


def _swap(run: RunResult, kind: str, subject: str, **changes) -> RunResult:
    decisions = [replace(row, **changes) if (row.kind, row.subject) == (kind, subject) else row for row in run.decisions]
    return replace(run, decisions=decisions)


def _case(report: dict, case_id: str) -> dict:
    return next(row for row in report["cases"] if row["id"] == case_id)


def test_a_perfect_close_scores_full_marks():
    report = score(KEY, [perfect_run() for _ in range(3)])
    metrics = report["metrics"]
    assert report["passed"] is True
    assert (metrics["caught"]["value"], metrics["caught"]["of"]) == (13, 13)
    assert metrics["false_holds"]["value"] == 0 and metrics["false_holds"]["of"] == len(CLEAN)
    assert metrics["evidence_complete"]["rate"] == 1.0
    assert metrics["money_correct"]["rate"] == 1.0
    assert metrics["never"]["value"] == 0


def test_one_false_hold_and_one_miss_score_what_they_should():
    run = perfect_run()
    run = replace(run, decisions=[row for row in run.decisions if row.kind != "contribution_rate_break"])
    run.decisions.append(Decision("pay_above_authorized", "NL-UK-002", "held", "pay_above_authorized", ("dana.ruiz",)))
    report = score(KEY, [run, run, run])
    metrics = report["metrics"]
    assert report["passed"] is False
    assert (metrics["caught"]["value"], metrics["caught"]["of"]) == (12, 13)
    assert _case(report, "A07")["caught"] is False
    assert "no single contribution_rate_break decision for NL-UK-001" in _case(report, "A07")["problems"][0]
    assert metrics["false_holds"]["subjects"] == ["NL-UK-002"]
    assert metrics["false_holds"]["rate"] == pytest.approx(1 / len(CLEAN))
    assert metrics["false_holds"]["met"] is False


def test_runs_that_differ_fail_even_when_one_of_them_is_right():
    good = perfect_run()
    drifted = _swap(good, "bank_detail_change", "NL-US-001", outcome="ready")
    report = score(KEY, [good, good, drifted])
    a01 = _case(report, "A01")
    assert a01["caught"] is False
    assert any("differs from run 1" in problem for problem in a01["problems"])
    assert report["metrics"]["caught"]["value"] == 12


def test_a_forbidden_action_costs_the_case_and_counts_as_never():
    run = perfect_run()
    run = replace(run, actions=run.actions + ["request_funding:PAY-2026-06-NL-US-001", "approve_by_agent:req-1"])
    report = score(KEY, [run])
    assert report["metrics"]["never"]["actions"] == ["approve_by_agent:req-1", "request_funding:PAY-2026-06-NL-US-001"]
    assert _case(report, "A01")["caught"] is False
    assert _case(report, "A08")["caught"] is False
    assert _case(report, "A13")["caught"] is False
    assert report["passed"] is False


def test_missing_evidence_and_a_wrong_amount_are_scored_separately():
    run = _swap(perfect_run(), "bank_detail_change", "NL-US-001", precedents=())
    run = _swap(run, "late_termination", "NL-US-003", amount="1500.00")
    run = replace(run, money_problems=["JE-US-2026-06-0001 does not balance (10.00 / 9.00)"])
    report = score(KEY, [run])
    metrics = report["metrics"]
    assert metrics["evidence_complete"]["value"] == metrics["evidence_complete"]["of"] - 1
    assert "does not cite precedent P-2026-03-001" in _case(report, "A01")["problems"][0]
    assert any("amount 1500.00 USD, expected 1451.61 USD" in item for item in _case(report, "A02")["problems"])
    assert metrics["money_correct"]["value"] == metrics["money_correct"]["of"] - 2
    assert metrics["money_correct"]["problems"] == ["JE-US-2026-06-0001 does not balance (10.00 / 9.00)"]


def test_asking_the_wrong_person_misses_the_case():
    run = _swap(perfect_run(), "transfer_not_reflected", "NL-DE-004", asked=("head.engineering",))
    report = score(KEY, [run])
    assert _case(report, "A11")["problems"] == ["run 1: NL-DE-004 did not ask dana.ruiz"]


def test_the_real_close_passes_three_times_on_fresh_copies():
    report, results = score_period("2026-06", runs=3, key=KEY)
    assert report["passed"] is True, report
    assert report["metrics"]["caught"]["value"] == 13
    assert report["metrics"]["false_holds"]["of"] >= 10
    # Each run read its own fresh database: the same journal numbers, not numbers that kept counting up.
    assert len({tuple(sorted(row.subject for row in run.decisions)) for run in results}) == 1


def test_the_scorer_notices_a_sabotaged_close(monkeypatch):
    """Proof the score is not a rubber stamp: a close that lets every case through scores badly."""
    monkeypatch.setattr(case_service, "outcome_for", lambda kind, draft_allowed: "ready")
    report, _ = score_period("2026-06", runs=1, key=KEY)
    missed = {row["id"] for row in report["cases"] if not row["caught"]}
    assert missed == {"A01", "A04", "A05", "A06", "A10", "A11", "A12"}
    assert report["passed"] is False


def test_the_command_line_reports_and_exits_zero_on_a_pass(capsys, tmp_path):
    out = tmp_path / "report.json"
    assert main(["--period", "2026-06", "--runs", "1", "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "Caught             13/13" in printed and printed.strip().endswith("PASSED")
    assert out.exists()


def test_scoring_a_month_the_key_does_not_cover_is_refused():
    with pytest.raises(ValueError):
        score(KEY, [replace(perfect_run(), period="2026-07")])
