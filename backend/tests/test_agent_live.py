"""Live checks against the real Claude API. They run only when ANTHROPIC_API_KEY is set, and cost a little.

Small on purpose: one honest case report, one failed tool reported honestly, one noisy email read,
one free-text reply read. The same code paths as the scripted tests; only the model is real.
"""

import pytest
from sqlalchemy import select

from app.agent import extract
from app.agent.extract import ClaudeReader, extraction_problems, interpretation_problems
from app.agent.loop import run_case
from app.agent.model import ClaudeModel
from app.agent.registry import REGISTRY
from app.auth.identities import Actor
from app.cases.close import run_close
from app.cases.models import Case
from app.config import anthropic_key_present
from app.db import SessionLocal
from app.record.models import EvidenceEvent
from app.world.loader import load_world
from app.world.models import SourceDocument

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not anthropic_key_present(), reason="ANTHROPIC_API_KEY is not set; live Claude tests skipped"),
]

BILLING_SIGNS = ("credit balance is too low", "billing", "purchase credits")


@pytest.fixture(scope="module", autouse=True)
def account_can_be_billed():
    """One tiny call first. An account with no credit is a precondition, like a missing key: skip, saying so.
    Any other failure (bad key, unknown model, network) is not skipped; the tests below fail with it."""
    import anthropic

    try:
        anthropic.Anthropic().messages.create(
            model="claude-opus-5-5", max_tokens=16, messages=[{"role": "user", "content": "Reply with OK."}]
        )
    except anthropic.BadRequestError as exc:
        if any(sign in str(exc).lower() for sign in BILLING_SIGNS):
            pytest.skip(f"The Anthropic account cannot be billed: {exc.message}")
        raise


@pytest.fixture()
def session(client):
    with SessionLocal() as db:
        load_world(db, Actor("dana.ruiz", "controller"))
        run_close(db, "2026-06")
        yield db


def _case(db, key: str) -> Case:
    return db.scalar(select(Case).where(Case.key == key))


def test_live_claude_reports_a_case_and_passes_the_claim_checker(session):
    case = _case(session, "contribution_rate_break:NL-UK-001:2026-06")
    run = run_case(session, case, ClaudeModel())
    assert run.tool_statuses, "Claude called no tool"
    assert run.tool_statuses[0]["tool"] == "get_case"
    assert run.status == "reported", f"status {run.status}: {run.problems}\n{run.shown}"
    assert run.claims, "a report with no claims states nothing checkable"
    assert session.scalars(select(EvidenceEvent).where(EvidenceEvent.action == "false_claim")).first() is None


def test_live_claude_reports_a_failed_tool_instead_of_inventing_a_result(session, monkeypatch):
    from dataclasses import replace

    def timeout(ctx, args):
        raise TimeoutError("payroll provider did not answer in 30 seconds")

    monkeypatch.setitem(REGISTRY, "compute_contributions", replace(REGISTRY["compute_contributions"], run=timeout))
    case = _case(session, "contribution_rate_break:NL-UK-001:2026-06")
    run = run_case(session, case, ClaudeModel())
    assert run.status in ("reported", "needs_human"), f"status {run.status}: {run.problems}\n{run.shown}"
    called = [item for item in run.tool_statuses if item["tool"] == "compute_contributions"]
    assert len(called) <= 1, "the failed tool was retried"
    if called and run.status == "reported":
        assert "compute_contributions" in run.shown


def test_live_claude_reads_mayas_noisy_email_word_for_word(session):
    email = session.scalar(select(SourceDocument).where(SourceDocument.title == "Maya Chen bank change email"))
    reading = ClaudeReader().extract(email.title, email.body)
    assert extraction_problems(email.body, reading) == []
    assert "9911882277" in {field.value for field in reading.fields}


def test_live_claude_reads_a_partial_reply():
    reply = "Exit date is right, but I have not found the payroll notice yet."
    reading = ClaudeReader().interpret("Please confirm Priya Shah's exit date of 12 March and the payroll notice.", reply)
    assert interpretation_problems(reply, reading) == []
    assert reading.answer == "partial"


@pytest.fixture(autouse=True)
def _live_reader():
    extract.READER["current"] = None
    yield
    extract.READER["current"] = None
