from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class SimClock(Base):
    """The simulated date. "Three days later" is one controller click, and every wait is measured on it."""

    __tablename__ = "sim_clock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    today: Mapped[str] = mapped_column(String(10), nullable=False)


class ApprovalRequest(Base):
    """A request for a person to approve one exact thing. The binding hash pins what was asked for."""

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(36))
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(48), nullable=False)
    requested_on: Mapped[str] = mapped_column(String(10), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_json: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    approvals_needed: Mapped[int] = mapped_column(Integer, nullable=False)
    needs_controller: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # JSON list of the roles whose inbox shows this request; escalation adds to it
    inbox_roles: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    due_on: Mapped[str] = mapped_column(String(10), nullable=False)
    reminders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    replaced_by: Mapped[str | None] = mapped_column(String(36))


class ApprovalDecision(Base):
    """One person's answer to one request. Never changed; the binding hash shows what they saw."""

    __tablename__ = "approval_decisions"
    __table_args__ = (UniqueConstraint("request_id", "decided_by", name="one_decision_per_person"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.id"), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(48), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_on: Mapped[str] = mapped_column(String(10), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Callback(Base):
    """HR phoned the employee on a number already on file to confirm a bank-detail change (BNK-1)."""

    __tablename__ = "callbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    account: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    called_by: Mapped[str] = mapped_column(String(48), nullable=False)
    called_on: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FundingRequest(Base):
    """Vestly asks treasury to send money: a batch of payment lines, each with its own idempotency key."""

    __tablename__ = "funding_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(48), nullable=False)
    requested_on: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    # pending, approved, released, confirmed, declined, voided
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")


class FundingLine(Base):
    """One payment in a request. The idempotency key is the payment id, so the bank pays it at most once."""

    __tablename__ = "funding_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(ForeignKey("funding_requests.id"), nullable=False)
    payment_id: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(32), nullable=False)
    # requested, sent, confirmed, voided
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    sends: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BankTransfer(Base):
    """What the simulated bank executed. One row per idempotency key, never changed: the bank's own record."""

    __tablename__ = "bank_transfers"
    __table_args__ = (UniqueConstraint("idempotency_key", name="bank_pays_a_key_once"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payment_id: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(32), nullable=False)
    executed_on: Mapped[str] = mapped_column(String(10), nullable=False)
    instructed_by: Mapped[str] = mapped_column(String(48), nullable=False)
