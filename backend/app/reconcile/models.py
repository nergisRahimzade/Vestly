from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class Reconciliation(Base):
    __tablename__ = "reconciliations"
    __table_args__ = (
        UniqueConstraint("period", "fact", "subject_number", name="one_finding_per_subject"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
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
