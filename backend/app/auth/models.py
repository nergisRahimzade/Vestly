from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class Identity(Base):
    __tablename__ = "identities"

    handle: Mapped[str] = mapped_column(String(48), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    department: Mapped[str | None] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class AccessLog(Base):
    """Every read of one person's pay or bank details."""

    __tablename__ = "access_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    handle: Mapped[str] = mapped_column(String(48), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str | None] = mapped_column(String(7))
    subjects: Mapped[str] = mapped_column(String, nullable=False)
