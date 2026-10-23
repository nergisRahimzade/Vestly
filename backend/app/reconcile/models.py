from datetime import datetime

from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class ReconciliationRun(Base):
    """One pass of the reconciler. Runs are never edited or deleted, so earlier answers stay readable."""

    __tablename__ = "reconciliation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    ran_by: Mapped[str] = mapped_column(String(48), nullable=False)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_documents: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (UniqueConstraint("period", "sequence", name="one_run_number_per_period"),)


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    __table_args__ = (
        UniqueConstraint("run_id", "kind", "fact", "subject_number", name="one_finding_per_subject_per_run"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    fact: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_number: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_name: Mapped[str] = mapped_column(String(120), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    authority: Mapped[str] = mapped_column(String(32), nullable=False)
    trusted_value: Mapped[str] = mapped_column(String(64), nullable=False)
    explanation: Mapped[str] = mapped_column(String, nullable=False)
    ranking_json: Mapped[str] = mapped_column(String, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # What a detector concluded, beyond the ranked claims above.
    kind: Mapped[str] = mapped_column(String(48), nullable=False, default="employment_dates_conflict")
    entity_code: Mapped[str | None] = mapped_column(String(8))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    ask_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    materiality: Mapped[str | None] = mapped_column(String(16))
    materiality_reason: Mapped[str | None] = mapped_column(String(200))
    draft_allowed: Mapped[bool | None] = mapped_column(Boolean)
    basis_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    details_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")
