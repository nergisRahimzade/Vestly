from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class EquityGrantMonth(Base):
    """One grant's subledger line for one month. Rebuilt from signed grants and HR facts."""

    __tablename__ = "equity_grant_months"
    __table_args__ = (UniqueConstraint("grant_number", "period", name="one_grant_month"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
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
