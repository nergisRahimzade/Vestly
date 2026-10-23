"""Amounts used in a payroll draft. Policy sets the contribution; the register keeps the rest."""

from datetime import date, timedelta
from decimal import Decimal

from app.ledger.money import CENT


def quantize(amount: Decimal) -> Decimal:
    return amount.quantize(CENT)


def policy_contribution(gross: Decimal, rate: Decimal) -> Decimal:
    return quantize(gross * rate)


def overpaid_gross(register_gross: Decimal, paid_from: str, paid_through: str, termination_date: str) -> Decimal:
    """Share of a register line that covers days after the HR last day."""
    start = date.fromisoformat(paid_from)
    end = date.fromisoformat(paid_through)
    last_day = date.fromisoformat(termination_date)
    if end <= last_day:
        return Decimal("0.00")
    # Only the days of this pay period after the last day: someone who left years ago is overpaid this whole period, no more.
    extra_start = max(start, last_day + timedelta(days=1))
    extra_days = (end - extra_start).days + 1
    total_days = (end - start).days + 1
    return quantize(register_gross * extra_days / total_days)


def accrual_days(pay_period_end: str, month_end: str) -> int:
    end = date.fromisoformat(pay_period_end)
    close = date.fromisoformat(month_end)
    if end >= close:
        return 0
    return (close - end).days


def month_length(month_end: str) -> int:
    close = date.fromisoformat(month_end)
    return close.day


def accrue_wages(monthly_rate: Decimal, days: int, days_in_month: int) -> Decimal:
    if days <= 0:
        return Decimal("0.00")
    return quantize(monthly_rate * days / days_in_month)
