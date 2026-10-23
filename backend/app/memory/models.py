from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class Observation(Base):
    """A pattern Vestly learned from the company's history, with the months it rests on. Append-only.

    The agent cites an observation like any other evidence: its value, its months, its evidence row.
    A later close that learns a different value writes a new observation; the old one stays.
    """

    __tablename__ = "observations"
    __table_args__ = (UniqueConstraint("kind", "subject", "as_of", "value_sha256", name="one_observation_per_value"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    # entity:department, or an employee number
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of: Mapped[str] = mapped_column(String(7), nullable=False)
    value_json: Mapped[str] = mapped_column(String, nullable=False)
    months_json: Mapped[str] = mapped_column(String, nullable=False)
    value_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(16), nullable=False)
    learned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
