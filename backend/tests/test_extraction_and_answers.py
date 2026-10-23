"""Task 13: reading documents and people's replies. The model reads; code checks every value against the words."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agent import extract
from app.agent.execute import AgentContext, execute
from app.agent.extract import ClaudeReader, DocumentExtraction, ReplyInterpretation, ScriptedReader, extraction_problems
from app.agent.loop import run_case
from app.agent.model import ScriptedModel
from app.auth.identities import Actor
from app.cases.close import run_close
from app.cases.models import Case, CaseQuestion
from app.db import SessionLocal
from app.world.loader import load_world
from app.world.models import SourceDocument
from helpers import AUDITOR, CONTROLLER, HR_OPS, PAYROLL_AGENT, as_user

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "agent"
DOCUMENTS = json.loads((FIXTURES / "documents.json").read_text(encoding="utf-8"))
REPLIES = json.loads((FIXTURES / "replies.json").read_text(encoding="utf-8"))
MAYA_EMAIL = "Maya Chen bank change email"


@pytest.fixture(autouse=True)
def scripted_reader():
    extract.READER["current"] = ScriptedReader()
    yield
    extract.READER["current"] = None


@pytest.fixture()
def session(client):
    with SessionLocal() as db:
        load_world(db, Actor("dana.ruiz", "controller"))
        run_close(db, "2026-06")
        yield db


def _maya(db) -> tuple[AgentContext, str]:
    case = db.scalar(select(Case).where(Case.key == "bank_detail_change:NL-US-001:2026-06"))
    email = db.scalar(select(SourceDocument).where(SourceDocument.title == MAYA_EMAIL))
    return AgentContext(db, case, Actor("vestly.payroll", "agent"), "run-extract"), email.content_hash


def _field(result, name: str) -> dict:
    return next(field for field in result.data["fields"] if field["name"] == name)


# ---------------------------------------------------------------- documents


def test_mayas_noisy_email_yields_the_account_with_its_quote(session):
    ctx, digest = _maya(session)
    result = execute(ctx, "extract_document", {"content_hash": digest})
    assert result.status == "ok", result.error
    account = _field(result, "requested_account")
    assert account == {"name": "requested_account", "value": "9911882277", "quote": "New acount: 9911882277"}
    assert _field(result, "sender")["value"] == "maya.chen@northlight.example"
    assert result.data["document_kind"] == "bank_detail_change"
    assert result.establishes == ((digest, "found"),)


def test_an_extraction_that_adds_a_value_not_in_the_text_is_refused(session):
    ctx, digest = _maya(session)
    for invented in (
        {"name": "requested_account", "value": "9911882299", "quote": "New acount: 9911882299"},
        {"name": "routing", "value": "111000222", "quote": "Routng: 222000333"},
    ):
        bad = DocumentExtraction(document_kind="bank_detail_change", fields=[invented])
        extract.READER["current"] = ScriptedReader(extractions={MAYA_EMAIL: bad})
        result = execute(ctx, "extract_document", {"content_hash": digest})
        assert result.status == "refused"
        assert result.error.startswith("Extraction refused")
        assert invented["name"] in result.error


def test_a_document_the_case_does_not_cite_cannot_be_read(session):
    ctx, _ = _maya(session)
    other = session.scalar(select(SourceDocument).where(SourceDocument.title == "Controller rejection of Jonah Hale bank change"))
    result = execute(ctx, "extract_document", {"content_hash": other.content_hash})
    assert result.status == "refused" and result.error.startswith("PRV-1")


def test_a_model_that_declines_to_read_is_a_failed_result(session):
    class Declines:
        def extract(self, title, text):
            raise extract.ExtractionRefused("The model declined to read this text.")

    extract.READER["current"] = Declines()
    ctx, digest = _maya(session)
    result = execute(ctx, "extract_document", {"content_hash": digest})
    assert result.status == "failed" and result.error == "extraction_declined: The model declined to read this text."


@pytest.mark.parametrize("document", DOCUMENTS["documents"], ids=lambda row: row["title"])
def test_the_synthetic_documents_read_word_for_word(document):
    reading = ScriptedReader().extract(document["title"], document["text"])
    assert extraction_problems(document["text"], reading) == []
    expected = dict(document["expected"])
    assert reading.document_kind == expected.pop("document_kind")
    found = {field.name: field.value for field in reading.fields}
    for name, value in expected.items():
        assert found[name] == value


@pytest.mark.parametrize("bad", DOCUMENTS["bad_readings"], ids=lambda row: row["why"][:40])
def test_the_bad_readings_are_caught(bad):
    text = next(row["text"] for row in DOCUMENTS["documents"] if row["title"] == bad["title"])
    assert extraction_problems(text, DocumentExtraction.model_validate(bad["reading"]))


def test_the_bank_change_case_reads_the_email_and_still_holds(session):
    ctx, _ = _maya(session)
    run = run_case(session, ctx.case, ScriptedModel())
    assert run.status == "reported", run.problems
    tools = [item["tool"] for item in run.tool_statuses]
    assert "extract_document" in tools
    assert {item["status"] for item in run.tool_statuses} == {"ok"}
    session.refresh(ctx.case)
    assert ctx.case.outcome == "held"


def test_the_claude_reader_sends_a_low_effort_structured_request():
    sent = []

    class Messages:
        def parse(self, **kwargs):
            sent.append(kwargs)
            parsed = kwargs["output_format"](document_kind="other", fields=[]) if kwargs["output_format"] is DocumentExtraction else None
            return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed)

    reader = ClaudeReader(client=SimpleNamespace(messages=Messages()))
    assert reader.extract("Note", "Hello").document_kind == "other"
    request = sent[0]
    assert request["model"] == "claude-opus-5-5" and request["output_config"] == {"effort": "low"}
    assert request["output_format"] is DocumentExtraction
    document = request["messages"][0]["content"][0]
    assert document == {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "Hello"}, "title": "Note"}

    class Refuses:
        def parse(self, **kwargs):
            return SimpleNamespace(stop_reason="refusal", parsed_output=None)

    with pytest.raises(extract.ExtractionRefused):
        ClaudeReader(client=SimpleNamespace(messages=Refuses())).interpret("q", "a")


# ---------------------------------------------------------------- replies


@pytest.mark.parametrize("reply", REPLIES["replies"], ids=lambda row: row["text"][:40])
def test_the_synthetic_replies_are_read_as_expected(reply):
    reading = ScriptedReader().interpret("Is this right?", reply["text"])
    assert reading.answer == reply["answer"]
    assert reading.corrected_value == reply.get("corrected_value")
    assert extract.interpretation_problems(reply["text"], reading) == []


def _question(client, key: str, to: str) -> dict:
    case = next(row for row in client.get("/cases?period=2026-06", headers=AUDITOR).json() if row["key"] == key)
    timeline = client.get(f"/cases/{case['id']}", headers=AUDITOR).json()
    return next(row for row in timeline["questions"] if row["asked_to"] == to and row["status"] == "open")


@pytest.fixture()
def closed_api(client):
    assert client.post("/world/load", headers=CONTROLLER).status_code == 200
    assert client.post("/close?period=2026-06", headers=PAYROLL_AGENT).status_code == 200
    return client


def test_a_partial_free_text_answer_counts_only_once_confirmed_and_then_triggers_a_follow_up(closed_api):
    client = closed_api
    question = _question(client, "late_termination:NL-US-003:2026-06", "lee.park")
    text = "Exit date is right, but I have not found the payroll notice yet."
    draft = client.post(f"/questions/{question['id']}/reply", headers=HR_OPS, json={"text": text}).json()
    assert (draft["status"], draft["counts_as"]) == ("proposed", "partial")
    assert draft["reading"]["quote"] == text

    # Nothing counts yet: the question is still open and the case is still waiting.
    still = _question(client, "late_termination:NL-US-003:2026-06", "lee.park")
    assert still["id"] == question["id"] and still["answers"] == []

    case = client.post(f"/questions/replies/{draft['id']}/confirm", headers=HR_OPS, json={"accept": True}).json()
    assert case["state"] == "waiting_on_human"
    assert [step["to"] for step in case["steps"]][-2:] == ["partially_answered", "waiting_on_human"]
    follow_up = next(row for row in case["questions"] if row["asked_to"] == "lee.park" and row["status"] == "open")
    assert follow_up["id"] != question["id"] and "payroll notice" in follow_up["question"]
    answered = next(row for row in case["questions"] if row["id"] == question["id"])
    assert answered["answers"][0]["answer"] == "partial" and text in answered["answers"][0]["note"]


def test_a_correction_holds_the_case_with_the_corrected_value(closed_api):
    client = closed_api
    question = _question(client, "late_termination:NL-US-003:2026-06", "lee.park")
    draft = client.post(f"/questions/{question['id']}/reply", headers=HR_OPS, json={"text": "The termination date should be 2026-03-12, not the 15th."}).json()
    assert (draft["counts_as"], draft["reading"]["corrected_value"]) == ("declined", "2026-03-12")
    case = client.post(f"/questions/replies/{draft['id']}/confirm", headers=HR_OPS, json={"accept": True}).json()
    assert (case["state"], case["outcome"]) == ("held", "held")
    assert "Corrected value: 2026-03-12" in case["questions"][0]["answers"][0]["note"]


def test_a_wrong_reading_is_rejected_by_the_person_and_counts_for_nothing(closed_api):
    client = closed_api
    question = _question(client, "late_termination:NL-US-003:2026-06", "lee.park")
    draft = client.post(f"/questions/{question['id']}/reply", headers=HR_OPS, json={"text": "Yes, that is right."}).json()
    other = client.post(f"/questions/replies/{draft['id']}/confirm", headers=as_user("morgan.li"), json={"accept": True})
    assert (other.status_code, other.json()["rule"]) == (403, "ACC-1")
    client.post(f"/questions/replies/{draft['id']}/confirm", headers=HR_OPS, json={"accept": False})
    assert _question(client, "late_termination:NL-US-003:2026-06", "lee.park")["answers"] == []
    again = client.post(f"/questions/replies/{draft['id']}/confirm", headers=HR_OPS, json={"accept": True})
    assert again.status_code == 409


def test_a_reading_that_does_not_hold_up_is_never_put_to_the_person(closed_api):
    client = closed_api
    question = _question(client, "late_termination:NL-US-003:2026-06", "lee.park")
    text = "Let me check with the manager."
    extract.READER["current"] = ScriptedReader(
        replies={text: ReplyInterpretation(answer="confirmed", corrected_value=None, note="Confirmed the exit date.", quote="Yes, confirmed.")}
    )
    draft = client.post(f"/questions/{question['id']}/reply", headers=HR_OPS, json={"text": text}).json()
    assert draft["status"] == "unreadable"
    assert draft["problems"] == ["The quote 'Yes, confirmed.' is not in the reply."]
    refused = client.post(f"/questions/replies/{draft['id']}/confirm", headers=HR_OPS, json={"accept": True})
    assert refused.status_code == 409


def test_only_the_person_asked_may_reply(closed_api):
    client = closed_api
    question = _question(client, "late_termination:NL-US-003:2026-06", "lee.park")
    refused = client.post(f"/questions/{question['id']}/reply", headers=as_user("morgan.li"), json={"text": "Yes."})
    assert (refused.status_code, refused.json()["rule"]) == (403, "ACC-1")
    agent = client.post(f"/questions/{question['id']}/reply", headers=PAYROLL_AGENT, json={"text": "Yes."})
    assert (agent.status_code, agent.json()["rule"]) == (403, "ACC-1")
