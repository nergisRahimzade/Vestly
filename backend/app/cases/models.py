from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class Case(Base):
    """One problem on one subject in one month: the work item everything else hangs off.

    state is where the work is (open, waiting on a person, posted...). outcome is what code
    decided from the recorded results: held, ready, rejected_duplicate or needs_human. Both
    change over time; every change is a CaseTransition row, which is never edited.
    """

    __tablename__ = "cases"
    __table_args__ = (UniqueConstraint("key", name="one_case_per_problem"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # kind:subject:period, so a second close finds the same case instead of opening another
    key: Mapped[str] = mapped_column(String(160), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_number: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_code: Mapped[str | None] = mapped_column(String(8))
    seat: Mapped[str] = mapped_column(String(48), nullable=False)
    finding_id: Mapped[str] = mapped_column(ForeignKey("reconciliations.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(24))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    opened_on: Mapped[str] = mapped_column(String(10), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaseTransition(Base):
    """One step of a case. Append-only: the case's history is these rows in order."""

    __tablename__ = "case_transitions"
    __table_args__ = (UniqueConstraint("case_id", "seq", name="one_step_number_per_case"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(48), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    on: Mapped[str] = mapped_column(String(10), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(16), nullable=False)


class CaseQuestion(Base):
    """Vestly asks one person one thing. Code picks who, from the policy's routing map."""

    __tablename__ = "case_questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False)
    asked_to: Mapped[str] = mapped_column(String(48), nullable=False)
    asked_by: Mapped[str] = mapped_column(String(48), nullable=False)
    question: Mapped[str] = mapped_column(String, nullable=False)
    asked_on: Mapped[str] = mapped_column(String(10), nullable=False)
    due_on: Mapped[str] = mapped_column(String(10), nullable=False)
    reminders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # open, answered, partially_answered, declined, expired
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    # the question this one escalates, when nobody answered it in time
    escalates: Mapped[str | None] = mapped_column(ForeignKey("case_questions.id"))
    # set once the case has acted on the answer, so a restart knows what is still to do
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CaseAnswer(Base):
    """A person's answer, as given. Append-only."""

    __tablename__ = "case_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    question_id: Mapped[str] = mapped_column(ForeignKey("case_questions.id"), nullable=False)
    answered_by: Mapped[str] = mapped_column(String(48), nullable=False)
    # confirmed, partial, declined
    answer: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    answered_on: Mapped[str] = mapped_column(String(10), nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(16), nullable=False)


class ReplyDraft(Base):
    """A person's free-text reply, as Vestly read it. It counts only after the same person confirms the reading."""

    __tablename__ = "reply_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    question_id: Mapped[str] = mapped_column(ForeignKey("case_questions.id"), nullable=False)
    replied_by: Mapped[str] = mapped_column(String(48), nullable=False)
    reply: Mapped[str] = mapped_column(String, nullable=False)
    # what the reader made of it: answer, corrected_value, note, quote
    interpretation_json: Mapped[str | None] = mapped_column(String)
    problems_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    # proposed (waiting for the person to confirm), confirmed, rejected (they said the reading is wrong),
    # unreadable (the reading did not hold up against their words)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CaseLink(Base):
    """A record that belongs to a case: a journal it drafted, a funding request it made. Append-only."""

    __tablename__ = "case_links"
    __table_args__ = (UniqueConstraint("case_id", "resource", "resource_id", name="one_link_per_record"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), nullable=False)
    resource: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
