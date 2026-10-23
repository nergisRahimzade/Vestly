"""Errors say where and why: which file, section, row, detector, tool, case or payment, and which line of code."""

import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.agent.execute import AgentContext, execute
from app.agent.loop import run_case
from app.agent.model import ClaudeModel
from app.agent.registry import REGISTRY
from app.auth.identities import Actor
from app.cases.close import run_close
from app.cases.models import Case
from app.db import ENGINE, SessionLocal
from app.main import app
from app.record.models import ErrorLog
from app.world.drops import build_drop
from app.world.loader import load_world
from helpers import AUDITOR, CONTROLLER, PAYROLL_AGENT, as_user

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _july_drop() -> dict:
    return build_drop("2026-07", closed=False)


def test_a_drop_with_an_unknown_field_names_the_section_the_row_and_the_field(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    drop = _july_drop()
    drop["payroll"][2]["gross_wage"] = drop["payroll"][2].pop("gross_wages")
    response = client.post("/world/drops", headers=CONTROLLER, json=drop)
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_drop"
    assert "storing the payroll section (period=2026-07, row=3)" in body["message"]
    assert "gross_wage" in body["message"]
    # Nothing of the broken drop was kept.
    assert client.get("/world/documents?period=2026-07", headers=CONTROLLER).json() == []


def test_a_drop_missing_a_required_value_names_the_column(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    drop = _july_drop()
    del drop["hr"][0]["bank_account"]
    response = client.post("/world/drops", headers=CONTROLLER, json=drop)
    assert response.status_code == 422
    assert "writing the drop's rows to the database (period=2026-07)" in response.json()["message"]
    assert "bank_account" in response.json()["message"]


def test_a_broken_detector_is_named_with_the_line_and_logged_under_an_error_id(client, monkeypatch):
    from app.reconcile import detectors

    assert client.post("/world/load", headers=CONTROLLER).status_code == 200

    def fx_band(ctx):
        return {}["gbp_usd_reference"]

    monkeypatch.setattr(detectors, "DETECTORS", (fx_band,))
    raw = TestClient(app, raise_server_exceptions=False)
    response = raw.post("/reconciliations?period=2026-06", headers=PAYROLL_AGENT)
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error" and body["error_id"].startswith("err_")
    assert body["message"] == "while running the fx_band detector (period=2026-06): KeyError: 'gbp_usd_reference'"
    assert body["where"].startswith("app/reconcile/detectors.py:") and body["where"].endswith("in detect_all")

    [logged] = [row for row in client.get("/audit/errors", headers=AUDITOR).json() if row["id"] == body["error_id"]]
    assert logged["request"] == "POST /reconciliations"
    assert logged["steps"] == [{"doing": "running the fx_band detector", "ids": {"period": "2026-06"}}]
    assert "test_error_context.py" in logged["traceback"] and "fx_band" in logged["traceback"]
    assert client.get("/audit/errors", headers=as_user("stranger")).status_code == 403


def test_a_crashing_tool_names_the_tool_and_the_case_and_is_logged(client, monkeypatch):
    with SessionLocal() as db:
        load_world(db, Actor("dana.ruiz", "controller"))
        run_close(db, "2026-06")
        case = db.scalar(select(Case).where(Case.key == "contribution_rate_break:NL-UK-001:2026-06"))

        def broken(ctx, args):
            return {"rows": []}["missing"]

        monkeypatch.setitem(REGISTRY, "compute_contributions", replace(REGISTRY["compute_contributions"], run=broken))
        result = execute(AgentContext(db, case, Actor("vestly.payroll", "agent"), "run-broken-tool"), "compute_contributions", {"entity_code": "UK"})
        assert result.status == "failed" and result.error == "KeyError: 'missing'"
        assert result.data["tool"] == "compute_contributions" and result.data["case"] == case.key
        # The broken line is in this test, outside app/, so the deepest line of Vestly's own code is where it called the tool.
        assert result.data["where"].startswith("app/agent/execute.py:") and result.data["where"].endswith("in _run")
        logged = db.get(ErrorLog, result.data["error_id"])
        assert logged.request == f"tool compute_contributions on case {case.key}" and logged.actor == "vestly.payroll"


def test_a_bank_failure_names_the_payment(client, monkeypatch):
    from app.controls import posting

    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    client.post("/close?period=2026-06", headers=PAYROLL_AGENT)
    funding = client.get("/funding?period=2026-06", headers=AUDITOR).json()[0]
    approval = client.get(f"/approvals?subject_id={funding['id']}", headers=AUDITOR).json()[0]

    def down(session, line, entity_code, by):
        raise OSError("bank gateway socket closed")

    monkeypatch.setattr(posting, "bank_execute", down)
    raw = TestClient(app, raise_server_exceptions=False)
    response = raw.post(f"/approvals/{approval['id']}/approve", headers=as_user("alex.moreau"))
    assert response.status_code == 500
    message = response.json()["message"]
    assert "while sending a payment to the bank (payment=PAY-2026-06-NL-US-002" in message
    assert "OSError: bank gateway socket closed" in message


def test_claude_api_failures_read_plainly_and_hand_the_case_to_a_person(client):
    with SessionLocal() as db:
        load_world(db, Actor("dana.ruiz", "controller"))
        run_close(db, "2026-06")
        cases = list(db.scalars(select(Case).where(Case.period == "2026-06").order_by(Case.key)))

        def failing(exc):
            def create(**kwargs):
                raise exc

            return ClaudeModel(client=SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create))))

        unreachable = run_case(db, cases[0], failing(anthropic.APIConnectionError(request=REQUEST)))
        assert unreachable.status == "needs_human"
        assert "agent turn: could not reach the Claude API" in unreachable.shown

        bad_key = anthropic.AuthenticationError("invalid x-api-key", response=httpx2.Response(401, request=REQUEST), body=None)
        refused = run_case(db, cases[1], failing(bad_key))
        assert "refused the credentials (401). Check ANTHROPIC_API_KEY." in refused.shown

        missing = anthropic.NotFoundError("not found", response=httpx2.Response(404, request=REQUEST), body=None)
        assert "model claude-opus-5-5 was not found (404)" in run_case(db, cases[2], failing(missing)).shown


def test_an_unreadable_policy_file_names_the_file(tmp_path):
    from app.policy.loader import _parse_policy

    broken = tmp_path / "broken.yaml"
    broken.write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="The policy file broken.yaml could not be read"):
        _parse_policy(broken)
    with pytest.raises(RuntimeError, match="missing.yaml"):
        _parse_policy(tmp_path / "missing.yaml")


def test_the_error_log_cannot_be_rewritten(client):
    from app.errors import record_error

    try:
        raise ValueError("planted")
    except ValueError as exc:
        error_id = record_error(exc, request="test")
    with ENGINE.begin() as connection:
        with pytest.raises(Exception, match="append_only"):
            connection.execute(text("UPDATE error_log SET message = 'nothing happened'"))
    with SessionLocal() as db:
        # No line of app/ is involved, so the raising line itself is named.
        assert db.get(ErrorLog, error_id).where.startswith("tests/test_error_context.py:")
        assert db.get(ErrorLog, error_id).message == "ValueError: planted"


def test_the_env_file_fills_gaps_and_never_overrides(tmp_path, monkeypatch):
    from app import config

    env = tmp_path / ".env"
    env.write_text("# keys\nVESTLY_TEST_ONE=from-file\nVESTLY_TEST_TWO='quoted'\nexport VESTLY_TEST_THREE=x\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILES", (env,))
    monkeypatch.setenv("VESTLY_TEST_ONE", "from-environment")
    monkeypatch.delenv("VESTLY_TEST_TWO", raising=False)
    monkeypatch.delenv("VESTLY_TEST_THREE", raising=False)
    loaded = config.load_env_files()
    assert os.environ["VESTLY_TEST_ONE"] == "from-environment"
    assert os.environ["VESTLY_TEST_TWO"] == "quoted" and os.environ["VESTLY_TEST_THREE"] == "x"
    assert sorted(loaded) == ["VESTLY_TEST_THREE", "VESTLY_TEST_TWO"]
    for name in loaded:
        monkeypatch.delenv(name)


def test_the_secrets_file_is_ignored_by_git():
    root = Path(__file__).resolve().parents[2]
    assert ".env" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
