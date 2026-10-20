from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

def _m(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)

# UK and DE numbers are PLACEHOLDERS until replaced with real current-year rules.
RATES = {
    "US": {"employee": {"Social Security": "0.062", "Medicare": "0.0145", "Income tax": "0.20"},
           "employer": {"Social Security": "0.062", "Medicare": "0.0145"}},
    "UK": {"employee": {"National Insurance": "0.08", "Other (placeholder)": "0.0145", "Income tax": "0.15"},
           "employer": {"National Insurance": "0.10"}},
    "DE": {"employee": {"Social contributions": "0.106", "Other (placeholder)": "0.0145", "Income tax": "0.55"},
           "employer": {"Social contributions": "0.20"}},
}

def _payroll(country, annual_salary, fraction=1):
    gross = _m(Decimal(str(annual_salary)) / 12 * Decimal(str(fraction)))
    employee = {n: _m(gross * Decimal(r)) for n, r in RATES[country]["employee"].items()}
    employer = {n: _m(gross * Decimal(r)) for n, r in RATES[country]["employer"].items()}
    withholding = sum(employee.values(), ZERO)
    return {
        "gross": gross,
        "net": gross - withholding,
        "withholding": withholding,
        "taxes": employee["Income tax"],
        "employer_taxes": sum(employer.values(), ZERO),
        "employee_breakdown": employee,
        "employer_breakdown": employer,
    }

def calculate_us_payroll(annual_salary, fraction=1):
    return _payroll("US", annual_salary, fraction)

def calculate_uk_payroll(annual_salary, fraction=1):
    return _payroll("UK", annual_salary, fraction)

def calculate_germany_payroll(annual_salary, fraction=1):
    return _payroll("DE", annual_salary, fraction)

def proration_fraction(hire_date, termination_date, year, month):
    """Share of the month worked. termination_date = last day worked (inclusive)."""
    dim = monthrange(year, month)[1]
    first, last = date(year, month, 1), date(year, month, dim)
    start = max(hire_date, first)
    end = min(termination_date, last) if termination_date else last
    if end < start:
        return Decimal(0)
    return Decimal((end - start).days + 1) / Decimal(dim)

def prorate(monthly_salary, days_worked, days_in_month):
    return monthly_salary * days_worked / days_in_month

def months_between(date1, date2):
    return (date2.year - date1.year) * 12 + (date2.month - date1.month)

def shares_vested(total_shares, grant_date, as_of_date, vest_years=4, cliff_months=12):
    months = months_between(grant_date, as_of_date)
    total_months = vest_years * 12
    if months < cliff_months:
        return 0
    if months >= total_months:
        return total_shares
    return total_shares * months // total_months      # whole shares only

def equity_expense_for_period(options, fair_value, grant_date, period_end,
                              vest_years=4, cliff_months=12, termination_date=None):
    """Straight-line monthly expense. Booked only in months 1..total_months after grant.
    In the termination month, also returns the reversal of unvested expense."""
    total_months = vest_years * 12
    k = months_between(grant_date, period_end)
    if k < 1 or k > total_months:
        return {"expense": ZERO, "reversal": ZERO}

    monthly = _m(Decimal(options) * Decimal(str(fair_value)) / total_months)

    if termination_date:
        t = (termination_date.year, termination_date.month)
        p = (period_end.year, period_end.month)
        if t < p:
            return {"expense": ZERO, "reversal": ZERO}      # already handled earlier
        if t == p:
            cumulative = _m(Decimal(options) * Decimal(str(fair_value)) * k / total_months)
            # after the cliff, cumulative expense equals vested value, so nothing reverses
            return {"expense": monthly, "reversal": cumulative if k < cliff_months else ZERO}

    return {"expense": monthly, "reversal": ZERO}