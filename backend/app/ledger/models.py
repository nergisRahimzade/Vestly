from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id() -> str:
    return str(uuid4())


class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False)
    normal_balance: Mapped[str] = mapped_column(String(6), nullable=False)


class Period(Base):
    __tablename__ = "periods"
    __table_args__ = (
        UniqueConstraint("entity_id", "year", "month", name="one_period_per_entity_month"),
        # reopened: a controller unlocked a closed month, with a reason, to post a correction.
        CheckConstraint("status IN ('open', 'locked', 'reopened')", name="period_status_known"),
        CheckConstraint(
            "status <> 'reopened' OR (reopened_by IS NOT NULL AND reopen_reason IS NOT NULL)",
            name="reopen_has_reason",
        ),
        CheckConstraint("month >= 1 AND month <= 12", name="period_month_valid"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="open")
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by: Mapped[str | None] = mapped_column(String(80))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_reason: Mapped[str | None] = mapped_column(String(500))

    @property
    def accepts_entries(self) -> bool:
        return self.status in ("open", "reopened")

    entity: Mapped[Entity] = relationship()


class JournalEntry(Base):
    """A journal moves through draft, then approved or rejected, then posted.

    A draft its preparer replaces is withdrawn, not deleted, so every proposal stays on record.

    The preparer can never be the reviewer. That rule is a database check, not only a service rule.

    booking_key names what the entry books, for example one person's overpayment from one month.
    Only one live entry (draft, approved or posted) may hold a key, so an error is booked once,
    in one period, however many later closes see it again.
    """

    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("number", name="journal_number_unique"),
        Index(
            "one_live_entry_per_booking_key",
            "booking_key",
            unique=True,
            sqlite_where=text("status IN ('draft', 'approved', 'posted')"),
            postgresql_where=text("status IN ('draft', 'approved', 'posted')"),
        ),
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected', 'posted', 'withdrawn')", name="journal_status_known"
        ),
        CheckConstraint("reviewed_by IS NULL OR reviewed_by <> prepared_by", name="preparer_is_not_reviewer"),
        CheckConstraint(
            "status IN ('draft', 'withdrawn') OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="reviewed_after_draft",
        ),
        CheckConstraint(
            "(status = 'posted') = (posted_by IS NOT NULL AND posted_at IS NOT NULL)",
            name="posted_has_poster",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), nullable=False)
    period_id: Mapped[str] = mapped_column(ForeignKey("periods.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    memo: Mapped[str] = mapped_column(String(500), nullable=False)
    booking_key: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    prepared_by: Mapped[str] = mapped_column(String(80), nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(String(500))
    posted_by: Mapped[str | None] = mapped_column(String(80))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdraw_reason: Mapped[str | None] = mapped_column(String(500))

    entity: Mapped[Entity] = relationship()
    period: Mapped[Period] = relationship()
    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry",
        cascade="all, delete-orphan",
        order_by="JournalLine.line_number",
    )


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        UniqueConstraint("entry_id", "line_number", name="line_number_unique"),
        CheckConstraint("debit >= 0 AND credit >= 0", name="line_amounts_non_negative"),
        CheckConstraint("NOT (debit > 0 AND credit > 0)", name="line_one_sided"),
        CheckConstraint("debit > 0 OR credit > 0", name="line_nonzero"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entry_id: Mapped[str] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    department: Mapped[str | None] = mapped_column(String(32))

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()
