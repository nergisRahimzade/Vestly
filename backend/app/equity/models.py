from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class EquityScheduleRun(Base):
    """One build of the subledger. Runs are kept, so a later question can read the schedule a draft used."""

    __tablename__ = "equity_schedule_runs"
    __table_args__ = (UniqueConstraint("sequence", name="one_schedule_run_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    through_period: Mapped[str] = mapped_column(String(7), nullable=False)
    built_by: Mapped[str] = mapped_column(String(48), nullable=False)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class EquityGrantMonth(Base):
    """One grant's subledger line for one month, in one schedule run.

    Amounts are in the grant's currency. booked_net_expense is the same net in the currency
    of the entity HR placed the person in that month.
    """

    __tablename__ = "equity_grant_months"
    __table_args__ = (UniqueConstraint("run_id", "grant_number", "period", name="one_grant_month_per_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("equity_schedule_runs.id"), nullable=False)
    grant_number: Mapped[str] = mapped_column(String(32), nullable=False)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(120), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    department: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    service_month: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_expense: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    reversal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    net_expense: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cumulative_expense: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vested_shares: Mapped[int] = mapped_column(Integer, nullable=False)
    vested_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    board_approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    vest_end: Mapped[str] = mapped_column(String(10), nullable=False)
    cliff_end: Mapped[str] = mapped_column(String(10), nullable=False)
    booking_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    booked_net_expense: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
