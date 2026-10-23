"""Build Northlight's March and June 2026 file drops.

The amounts are synthetic. Contribution rates are chosen so the pay math lands on cents:
US withholding 20%, employer tax 8%, 401(k) match 4%.
UK withholding 20%, employer tax 10%, pension 5% (Amira Khan's register uses 3%).
Germany withholding 25%, employer social contributions 20%.
Stock expense is grant fair value divided by 48 months.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

CENT = Decimal("0.01")
VEST_MONTHS = Decimal(48)
CURRENCIES = {"US": "USD", "UK": "GBP", "DE": "EUR"}
RATE_CARD = {
    "us_401k_match_rate": "0.04",
    "uk_pension_rate": "0.05",
    "de_social_rate": "0.20",
    "fx_band": "0.01",
    "gbp_usd_reference": "1.2750",
    "gbp_usd_reporting": "1.2700",
}
ENTITY_RATES = {
    "US": {"withholding": Decimal("0.20"), "employer_tax": Decimal("0.08"), "contribution": Decimal("0.04")},
    "UK": {"withholding": Decimal("0.20"), "employer_tax": Decimal("0.10"), "contribution": Decimal("0.05")},
    "DE": {"withholding": Decimal("0.25"), "employer_tax": Decimal("0.00"), "contribution": Decimal("0.20")},
}

PEOPLE: list[dict] = [
    {
        "employee_number": "NL-US-001",
        "legal_name": "Maya Chen",
        "entity_code": "US",
        "department": "Engineering",
        "hire_date": "2022-04-04",
        "monthly_rate": "15000.00",
        "bank_account": "1002003001",
        "grant": {"number": "GR-US-001", "date": "2024-03-01", "fair_value": "48000.00", "board_approved": True},
    },
    {
        "employee_number": "NL-US-002",
        "legal_name": "Jonah Hale",
        "entity_code": "US",
        "department": "Engineering",
        "hire_date": "2023-01-09",
        "monthly_rate": "12000.00",
        "bank_account": "1002003002",
        "grant": {"number": "GR-US-002", "date": "2024-03-01", "fair_value": "24000.00", "board_approved": True},
    },
    {
        "employee_number": "NL-US-003",
        "legal_name": "Priya Shah",
        "entity_code": "US",
        "department": "Product",
        "hire_date": "2024-02-01",
        "monthly_rate": "15000.00",
        "march_paycheck": "7500.00",
        "pay_period_end": "2026-03-15",
        "termination_date": "2026-03-12",
        "termination_recorded_on": "2026-04-02",
        "bank_account": "1002003003",
        "grant": {"number": "GR-US-003", "date": "2024-03-01", "fair_value": "48000.00", "board_approved": True},
    },
    {
        "employee_number": "NL-US-004",
        "legal_name": "Evan Brooks",
        "entity_code": "US",
        "department": "GTM",
        "hire_date": "2023-06-01",
        "monthly_rate": "10000.00",
        "june_monthly_rate": "12000.00",
        "june_retro_gross": "4000.00",
        "june_retro_effective_date": "2026-04-01",
        "bank_account": "1002003004",
        "grant": {"number": "GR-US-004", "date": "2024-03-01", "fair_value": "24000.00", "board_approved": True},
    },
    {
        "employee_number": "NL-US-005",
        "legal_name": "Riley Nguyen",
        "entity_code": "US",
        "department": "Engineering",
        "hire_date": "2024-11-01",
        "monthly_rate": "10000.00",
        "bank_account": "1002003005",
        "grant": {"number": "GR-US-005", "date": "2024-11-01", "fair_value": "36000.00", "board_approved": True},
    },
    {
        "employee_number": "PAY-0099",
        "legal_name": "Sam Ortiz",
        "entity_code": "US",
        "department": "G&A",
        "hire_date": "2026-06-01",
        "monthly_rate": "8000.00",
        "in_hr": False,
        "payroll_periods": ["2026-06"],
        "bank_account": "1002003099",
    },
    {
        "employee_number": "NL-UK-001",
        "legal_name": "Amira Khan",
        "entity_code": "UK",
        "department": "Engineering",
        "hire_date": "2022-09-01",
        "monthly_rate": "8000.00",
        "contribution_rate": "0.03",
        "bank_account": "2002003001",
        "grant": {"number": "GR-UK-001", "date": "2024-03-01", "fair_value": "48000.00", "board_approved": True},
    },
    {
        "employee_number": "NL-UK-002",
        "legal_name": "Owen Blake",
        "entity_code": "UK",
        "department": "GTM",
        "hire_date": "2024-05-01",
        "monthly_rate": "7000.00",
        "bank_account": "2002003002",
        "grant": {"number": "GR-UK-002", "date": "2024-05-01", "fair_value": "24000.00", "board_approved": True},
    },
    {
        "employee_number": "NL-UK-003",
        "legal_name": "Hannah Price",
        "entity_code": "UK",
        "department": "G&A",
        "hire_date": "2025-08-01",
        "monthly_rate": "6500.00",
        "termination_date": "2026-01-15",
        "termination_recorded_on": "2026-01-16",
        "bank_account": "2002003003",
        "grant": {"number": "GR-UK-003", "date": "2025-08-01", "fair_value": "24000.00", "board_approved": True},
    },
    {
        "employee_number": "NL-DE-001",
        "legal_name": "Lukas Weber",
        "entity_code": "DE",
        "department": "Engineering",
        "hire_date": "2024-03-01",
        "monthly_rate": "9000.00",
        "bank_account": "3002003001",
        "grant": {"number": "GR-DE-001", "date": "2024-03-01", "fair_value": "48000.00", "board_approved": False},
    },
    {
        "employee_number": "NL-DE-002",
        "legal_name": "Sofia Keller",
        "entity_code": "DE",
        "department": "Product",
        "hire_date": "2023-02-01",
        "monthly_rate": "8000.00",
        "bank_account": "3002003002",
        "grant": {"number": "GR-DE-002", "date": "2024-03-01", "fair_value": "24000.00", "board_approved": True},
    },
    {
        "employee_number": "NL-DE-003",
        "legal_name": "Greta Hoffmann",
        "entity_code": "DE",
        "department": "G&A",
        "hire_date": "2025-01-06",
        "monthly_rate": "7500.00",
        "bank_account": "3002003003",
        "grant": {"number": "GR-DE-003", "date": "2025-01-06", "fair_value": "24000.00", "board_approved": True},
    },
]


def money(amount: Decimal) -> str:
    return f"{amount.quantize(CENT):.2f}"


def add_months(iso_date: str, months: int) -> str:
    year, month, day = (int(part) for part in iso_date.split("-"))
    month_index = year * 12 + (month - 1) + months
    new_year, new_month = divmod(month_index, 12)
    return f"{new_year:04d}-{new_month + 1:02d}-{day:02d}"


def period_end(period: str) -> str:
    return f"{period}-31" if period.endswith("-03") else f"{period}-30"


def hr_view(person: dict, period: str) -> tuple[str, str | None, str | None]:
    terminated_on = person.get("termination_date")
    recorded_on = person.get("termination_recorded_on")
    if terminated_on is None or recorded_on is None or recorded_on > period_end(period):
        return "active", None, None
    return "terminated", terminated_on, recorded_on


def listed_in_hr(person: dict) -> bool:
    return person.get("in_hr", True)


def on_payroll(person: dict, period: str) -> bool:
    limited = person.get("payroll_periods")
    if limited is not None and period not in limited:
        return False
    if not listed_in_hr(person):
        return True
    status, _, _ = hr_view(person, period)
    return status == "active"


def monthly_rate(person: dict, period: str) -> Decimal:
    if period == "2026-06" and person.get("june_monthly_rate"):
        return Decimal(person["june_monthly_rate"])
    return Decimal(person["monthly_rate"])


def paycheck_gross(person: dict, period: str) -> Decimal:
    if period == "2026-03" and person.get("march_paycheck"):
        return Decimal(person["march_paycheck"])
    gross = monthly_rate(person, period)
    if period == "2026-06" and person.get("june_retro_gross"):
        return gross + Decimal(person["june_retro_gross"])
    return gross


def pay_bounds(person: dict, period: str) -> tuple[str, str]:
    if period == "2026-03" and person.get("pay_period_end"):
        return f"{period}-01", person["pay_period_end"]
    return f"{period}-01", period_end(period)


def contribution_rate(person: dict) -> Decimal:
    if person.get("contribution_rate"):
        return Decimal(person["contribution_rate"])
    return ENTITY_RATES[person["entity_code"]]["contribution"]


def pay_amounts(person: dict, period: str) -> dict[str, Decimal]:
    gross = paycheck_gross(person, period)
    rates = ENTITY_RATES[person["entity_code"]]
    withholding = (gross * rates["withholding"]).quantize(CENT)
    employer_tax = (gross * rates["employer_tax"]).quantize(CENT)
    contribution = (gross * contribution_rate(person)).quantize(CENT)
    return {
        "gross": gross,
        "withholding": withholding,
        "net": gross - withholding,
        "employer_tax": employer_tax,
        "contribution": contribution,
    }


def debit(account: str, amount: Decimal, department: str | None) -> dict:
    return {
        "account_code": account,
        "department": department,
        "debit": money(amount),
        "credit": "0.00",
    }


def credit(account: str, amount: Decimal) -> dict:
    return {"account_code": account, "department": None, "debit": "0.00", "credit": money(amount)}


def balanced(entry: dict) -> dict:
    debits = sum((Decimal(line["debit"]) for line in entry["lines"]), Decimal("0.00"))
    credits = sum((Decimal(line["credit"]) for line in entry["lines"]), Decimal("0.00"))
    if debits != credits or debits <= 0 or len(entry["lines"]) < 2:
        raise RuntimeError(f"{entry['memo']} does not balance: {debits} vs {credits}")
    return entry


def payroll_entry(entity: str, period: str, rows: list[dict]) -> dict:
    buckets: dict[tuple[str, str], Decimal] = {}
    nets = withholdings = taxes = contributions = Decimal("0.00")
    for row in rows:
        department = row["department"]
        for account, field in (
            ("5000", "gross_wages"),
            ("5100", "employer_tax"),
            ("5110", "employer_contribution"),
        ):
            amount = Decimal(row[field])
            if amount == 0:
                continue
            buckets[(account, department)] = buckets.get((account, department), Decimal("0.00")) + amount
        nets += Decimal(row["net_pay"])
        withholdings += Decimal(row["employee_withholdings"])
        taxes += Decimal(row["employer_tax"])
        contributions += Decimal(row["employer_contribution"])
    lines = [
        debit(account, amount, department)
        for (account, department), amount in sorted(buckets.items())
    ]
    lines.append(credit("2100", nets))
    lines.append(credit("2200", withholdings))
    if taxes:
        lines.append(credit("2210", taxes))
    if contributions:
        lines.append(credit("2220", contributions))
    month = "March" if period.endswith("-03") else "June"
    return balanced(
        {
            "entity_code": entity,
            "kind": "payroll",
            "memo": f"{entity} payroll for {month} 2026, as already booked",
            "lines": lines,
        }
    )


def equity_entry(entity: str, period: str, rows: list[dict]) -> dict:
    buckets: dict[str, Decimal] = {}
    for row in rows:
        buckets[row["department"]] = buckets.get(row["department"], Decimal("0.00")) + Decimal(row["monthly_expense"])
    lines = [debit("5200", amount, department) for department, amount in sorted(buckets.items())]
    lines.append(credit("3000", sum(buckets.values(), Decimal("0.00"))))
    month = "March" if period.endswith("-03") else "June"
    return balanced(
        {
            "entity_code": entity,
            "kind": "equity",
            "memo": f"{entity} stock compensation for {month} 2026, fed by the equity platform",
            "lines": lines,
        }
    )


def bank_entry(entity: str, period: str, instructions: list[dict]) -> dict:
    total = sum((Decimal(row["amount"]) for row in instructions), Decimal("0.00"))
    month = "March" if period.endswith("-03") else "June"
    return balanced(
        {
            "entity_code": entity,
            "kind": "bank",
            "memo": f"{entity} net pay cleared in {month} 2026, per bank",
            "lines": [debit("2100", total, None), credit("1000", total)],
        }
    )


def rate_card_body() -> str:
    return "\n".join(
        [
            "Northlight employer contribution rates. Account numbers and people are synthetic.",
            "US 401(k) match: 4% of gross wages.",
            "UK employer pension: 5% of gross wages.",
            "Germany employer social contributions: 20% of gross wages.",
            "A GBP to USD rate within 1% of the reference rate is an explained difference.",
            f"Reference GBPUSD {RATE_CARD['gbp_usd_reference']}. Management pack used {RATE_CARD['gbp_usd_reporting']}.",
            "",
        ]
    )


def emails(period: str) -> list[dict]:
    if period == "2026-03":
        return [
            {
                "system": "email",
                "title": "Jonah Hale bank change email",
                "body": "\n".join(
                    [
                        "From: jonah.hale@northlight.example",
                        "To: payroll@northlight.example",
                        "Date: 2026-03-04",
                        "Subject: Update direct deposit",
                        "",
                        "Pleae switch my salry deposit to the new acount.",
                        "Acount: 9911002002",
                        "Routing: 222000333",
                        "",
                        "Jonah Hale",
                        "",
                    ]
                ),
            },
            {
                "system": "email",
                "title": "Controller rejection of Jonah Hale bank change",
                "body": "\n".join(
                    [
                        "From: controller@northlight.example",
                        "To: payroll@northlight.example",
                        "Date: 2026-03-06",
                        "Subject: Re: Update direct deposit",
                        "",
                        "Rejected. An email is not a bank-detail change.",
                        "The HR bank master stands unless HR updates it and a second person approves.",
                        "Jonah Hale stays on account 1002003002.",
                        "",
                    ]
                ),
            },
        ]
    return [
        {
            "system": "email",
            "title": "Maya Chen bank change email",
            "body": "\n".join(
                [
                    "From: maya.chen@northlight.example",
                    "To: payrol1@northlight.example",
                    "Date: 2026-06-02",
                    "Subject: Payr0ll direct deposit update",
                    "",
                    "Hi payrol team,",
                    "Please updte my bank acount before the June run.",
                    "New acount: 9911882277",
                    "Routng: 222000333",
                    "Name: Maya Chen",
                    "Thnaks",
                    "",
                ]
            ),
        }
    ]


def equity_row(person: dict) -> dict:
    grant = person["grant"]
    fair_value = Decimal(grant["fair_value"])
    approved = bool(grant["board_approved"])
    return {
        "grant_number": grant["number"],
        "employee_number": person["employee_number"],
        "legal_name": person["legal_name"],
        "entity_code": person["entity_code"],
        "department": person["department"],
        "grant_date": grant["date"],
        "cliff_end": add_months(grant["date"], 12),
        "vest_end": add_months(grant["date"], 48),
        "shares": int(fair_value / Decimal(10)),
        "fair_value": money(fair_value),
        "currency": CURRENCIES[person["entity_code"]],
        "board_approved": approved,
        "board_approval_date": grant["date"] if approved else None,
        "platform_status": "vesting",
        "monthly_expense": money((fair_value / VEST_MONTHS).quantize(CENT)),
    }


def payroll_row(person: dict, period: str) -> dict:
    amounts = pay_amounts(person, period)
    start, end = pay_bounds(person, period)
    row = {
        "employee_number": person["employee_number"],
        "legal_name": person["legal_name"],
        "entity_code": person["entity_code"],
        "department": person["department"],
        "pay_period_start": start,
        "pay_period_end": end,
        "gross_wages": money(amounts["gross"]),
        "employee_withholdings": money(amounts["withholding"]),
        "net_pay": money(amounts["net"]),
        "employer_tax": money(amounts["employer_tax"]),
        "employer_contribution": money(amounts["contribution"]),
        "currency": CURRENCIES[person["entity_code"]],
        "destination_account": person["bank_account"],
        "retro_effective_date": None,
        "retro_gross": None,
    }
    if period == "2026-06" and person.get("june_retro_gross"):
        row["retro_effective_date"] = person["june_retro_effective_date"]
        row["retro_gross"] = money(Decimal(person["june_retro_gross"]))
    return row


def funding_rows(person: dict, period: str, paid: dict[str, Decimal]) -> list[dict]:
    currency = CURRENCIES[person["entity_code"]]
    number = person["employee_number"]
    if period == "2026-06" and person.get("june_retro_gross"):
        regular_net = (monthly_rate(person, period) * Decimal("0.80")).quantize(CENT)
        retro_net = paid["net"] - regular_net
        return [
            instruction(period, f"PAY-{period}-{number}", person, regular_net, currency, "net_pay"),
            instruction(period, f"PAY-{period}-{number}-RETRO", person, retro_net, currency, "retro_net_pay"),
        ]
    return [instruction(period, f"PAY-{period}-{number}", person, paid["net"], currency, "net_pay")]


def instruction(period: str, payment_id: str, person: dict, amount: Decimal, currency: str, kind: str) -> dict:
    return {
        "payment_id": payment_id,
        "employee_number": person["employee_number"],
        "legal_name": person["legal_name"],
        "entity_code": person["entity_code"],
        "amount": money(amount),
        "currency": currency,
        "destination_account": person["bank_account"],
        "kind": kind,
        "cleared": is_cleared(period, payment_id),
    }


def is_cleared(period: str, payment_id: str) -> bool:
    if period != "2026-06":
        return True
    held = {"PAY-2026-06-NL-US-002", "PAY-2026-06-NL-US-005"}
    return payment_id not in held


def hr_row(person: dict, period: str) -> dict:
    status, terminated_on, recorded_on = hr_view(person, period)
    annual = monthly_rate(person, period) * Decimal(12)
    return {
        "employee_number": person["employee_number"],
        "legal_name": person["legal_name"],
        "entity_code": person["entity_code"],
        "department": person["department"],
        "status": status,
        "hire_date": person["hire_date"],
        "termination_date": terminated_on,
        "termination_recorded_on": recorded_on,
        "annual_salary": money(annual),
        "currency": CURRENCIES[person["entity_code"]],
        "bank_account": person["bank_account"],
        "bank_routing": "111000025",
    }


def build_drop(period: str) -> dict:
    hr = [hr_row(person, period) for person in PEOPLE if listed_in_hr(person)]
    payroll = [payroll_row(person, period) for person in PEOPLE if on_payroll(person, period)]
    funding: list[dict] = []
    for person in PEOPLE:
        if on_payroll(person, period):
            funding.extend(funding_rows(person, period, pay_amounts(person, period)))
    equity = [equity_row(person) for person in PEOPLE if person.get("grant")]
    bank = [
        {
            "entity_code": row["entity_code"],
            "posted_on": period_end(period),
            "amount": row["amount"],
            "currency": row["currency"],
            "payment_id": row["payment_id"],
            "description": f"Net pay {row['legal_name']} {row['payment_id']}",
            "destination_account": row["destination_account"],
        }
        for row in funding
        if row["cleared"]
    ]
    ledger_entries = []
    for entity in ("US", "UK", "DE"):
        entity_payroll = [row for row in payroll if row["entity_code"] == entity]
        entity_equity = [row for row in equity if row["entity_code"] == entity]
        entity_funding = [row for row in funding if row["entity_code"] == entity and row["cleared"]]
        if period == "2026-03":
            ledger_entries.append(payroll_entry(entity, period, entity_payroll))
            ledger_entries.append(bank_entry(entity, period, entity_funding))
        ledger_entries.append(equity_entry(entity, period, entity_equity))
    received_on = "2026-04-02" if period == "2026-03" else "2026-07-02"
    return {
        "period": period,
        "description": "Synthetic Northlight file drop. People, account numbers, and amounts are fictional.",
        "received_on": received_on,
        "close_period": period == "2026-03",
        "rate_card": RATE_CARD,
        "hr": hr,
        "payroll": payroll,
        "funding": funding,
        "equity": equity,
        "bank": bank,
        "documents": [{"system": "rate_card", "title": "Employer contribution rate card", "body": rate_card_body()}]
        + emails(period),
        "ledger_entries": ledger_entries,
    }


def canonical(drop: dict) -> str:
    return json.dumps(drop, indent=2) + "\n"


def fixture_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "world"


def write_fixtures() -> None:
    directory = fixture_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for period in ("2026-03", "2026-06"):
        path = directory / f"{period}.json"
        path.write_text(canonical(build_drop(period)), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_fixtures()
