"""Northlight chart of accounts. One chart, three entity books."""

ENTITIES: tuple[tuple[str, str, str], ...] = (
    ("US", "Northlight Labs, Inc.", "USD"),
    ("UK", "Northlight Labs Ltd", "GBP"),
    ("DE", "Northlight Labs GmbH", "EUR"),
)

# code, name, type, normal balance
ACCOUNTS: tuple[tuple[str, str, str, str], ...] = (
    ("1000", "Cash", "asset", "debit"),
    ("1100", "Payroll clearing", "asset", "debit"),
    ("2100", "Wages payable", "liability", "credit"),
    ("2200", "Employee tax withholdings payable", "liability", "credit"),
    ("2210", "Employer taxes payable", "liability", "credit"),
    ("2220", "Pension and social contributions payable", "liability", "credit"),
    ("3000", "APIC - stock-based compensation", "equity", "credit"),
    ("5000", "Wage expense", "expense", "debit"),
    ("5100", "Employer tax expense", "expense", "debit"),
    ("5110", "Employer pension and social expense", "expense", "debit"),
    ("5200", "Stock-based compensation expense", "expense", "debit"),
    ("6100", "FX gain/loss", "expense", "debit"),
)

DEPARTMENTS: tuple[str, ...] = ("Engineering", "Product", "GTM", "G&A")

CONTROLLER_ROLE = "controller"
