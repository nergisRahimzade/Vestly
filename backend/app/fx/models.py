from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class FxRate(Base):
    """One day's exchange rates as Open Exchange Rates published them (USD base). Append-only.

    final is true for an end-of-day rate (a day already over when it was fetched), false for an
    intraday "latest" rate. Only a final rate for a month's last day can lock that month.
    """

    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("rate_date", "response_sha256", name="one_row_per_response"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rate_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    base: Mapped[str] = mapped_column(String(3), nullable=False)
    # the provider's own rates: units of each currency per 1 USD, as published
    rates_json: Mapped[str] = mapped_column(String, nullable=False)
    provider_timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    final: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
