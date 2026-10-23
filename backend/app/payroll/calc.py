"""Amounts used in a payroll draft. Policy sets the contribution; the register keeps the rest."""

from datetime import date, timedelta
from decimal import Decimal

from app.ledger.money import CENT, money_str
from app.world.drops import ENTITY_RATES


def quantize(amount: Decimal) -> Decimal:
    return amount.quantize(CENT)


def policy_contribution(gross: Decimal, rate: Decimal) -> Decimal:
    return quantize(gross * rate)


def riley_hand_calc() -> dict[str, str]:
    """Riley Nguyen, US Engineering, June. Gross 10000.00."""
    gross = Decimal("10000.00")
    rates = ENTITY_RATES["US"]
    withholding = quantize(gross * rates["withholding"])
    tax = quantize(gross * rates["employer_tax"])
    contribution = policy_contribution(gross, rates["contribution"])
    return {
        "gross_wages": money_str(gross),
        "employee_withholdings": money_str(withholding),
        "net_pay": money_str(gross - withholding),
        "employer_tax": money_str(tax),
        "employer_contribution": money_str(contribution),
    }


def overpaid_gross(register_gross: Decimal, paid_from: str, paid_through: str, termination_date: str) -> Decimal:
    """Share of a register line that covers days after the HR last day."""
    start = date.fromisoformat(paid_from)
    end = date.fromisoformat(paid_through)
    last_day = date.fromisoformat(termination_date)
    if end <= last_day:
        return Decimal("0.00")
    extra_start = last_day + timedelta(days=1)
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
