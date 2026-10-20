from decimal import Decimal, ROUND_HALF_UP
from pydantic import BaseModel, field_validator

CENT = Decimal("0.01")

# Each account name lives in ONE place, so spelling can never drift apart
SALARY_EXP = "Salary Expense"
EMPLOYER_TAX_EXP = "Employer Payroll Tax Expense"
BENEFITS_EXP = "Benefits Expense"
NET_PAY_PAYABLE = "Cash / Net Pay Payable"
WITHHOLDING_PAYABLE = "Employee Withholding Payable"
EMPLOYER_PAYABLE = "Employer Tax & Benefits Payable"
SBC_EXP = "Stock-Based Compensation Expense"
APIC = "Additional Paid-In Capital"

def money(x) -> Decimal:
  return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)

class Line(BaseModel):
  account: str
  debit: Decimal = Decimal("0.00")
  credit: Decimal = Decimal("0.00")

class JournalEntry(BaseModel):
  entity: str
  currency: str
  period: str
  description: str
  source: str
  lines: list[Line]
  department: str = ""

  @field_validator("lines")
  @classmethod
  def must_balance(cls, lines):
      d = sum((l.debit for l in lines), Decimal("0"))
      c = sum((l.credit for l in lines), Decimal("0"))
      if d != c:
          raise ValueError(f"Unbalanced entry: debits {d} != credits {c}")
      if any(l.debit and l.credit for l in lines):
          raise ValueError("A line cannot have both debit and credit")
      return lines

def _label(text, period, department):
  return f"{text} {period}" + (f" ({department})" if department else "")

def payroll_entry(entity, currency, period, gross, employer_taxes, employee_withholding,
                  net_pay, employer_benefits=0, department=""):
  gross, employer_taxes = money(gross), money(employer_taxes)
  employee_withholding, net_pay = money(employee_withholding), money(net_pay)
  employer_benefits = money(employer_benefits)

  lines = [
      Line(account=SALARY_EXP, debit=gross),
      Line(account=EMPLOYER_TAX_EXP, debit=employer_taxes),
      Line(account=BENEFITS_EXP, debit=employer_benefits),
      Line(account=NET_PAY_PAYABLE, credit=net_pay),
      Line(account=WITHHOLDING_PAYABLE, credit=employee_withholding),
      Line(account=EMPLOYER_PAYABLE, credit=employer_taxes + employer_benefits),
  ]
  lines = [l for l in lines if l.debit or l.credit]
  return JournalEntry(entity=entity, currency=currency, period=period,
                      description=_label(f"{entity} payroll", period, department),
                      source="payroll", lines=lines, department=department)

def equity_expense_entry(entity, currency, period, expense, department=""):
  amt = money(expense)
  return JournalEntry(entity=entity, currency=currency, period=period,
                      description=_label("Stock-based compensation", period, department),
                      source="equity_expense", department=department,
                      lines=[Line(account=SBC_EXP, debit=amt), Line(account=APIC, credit=amt)])

def forfeiture_reversal_entry(entity, currency, period, reversed_amount, department=""):
  amt = money(reversed_amount)
  return JournalEntry(entity=entity, currency=currency, period=period,
                      description=_label("Forfeiture reversal", period, department),
                      source="forfeiture", department=department,
                      lines=[Line(account=APIC, debit=amt), Line(account=SBC_EXP, credit=amt)])