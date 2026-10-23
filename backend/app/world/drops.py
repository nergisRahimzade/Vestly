"""Build Northlight's monthly file drops.

A month is built in three layers:

1. People and what really happened to them: hires, raises, terminations. A clean month
   built from people alone has no anomalies.
2. Plants: one source system getting something wrong in one month. Each scored anomaly
   is one plant, so a new scenario is a list of plants applied to any month.
3. Derived records: funding instructions come from the payroll register, bank lines from
   cleared funding, and the prior ledger from what had been booked by then.

The amounts are synthetic. Contribution rates are chosen so the pay math lands on cents:
US withholding 20%, employer tax 8%, 401(k) match 4%.
UK withholding 20%, employer tax 10%, pension 5%.
Germany withholding 25%, employer social contributions 20%.
Stock expense is grant fair value divided by 48 months.
"""

from __future__ import annotations

import calendar
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

CENT = Decimal("0.01")
VEST_MONTHS = 48
ENTITIES = ("US", "UK", "DE")
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

# Who works at Northlight, and what really happened to them.
# raises: effective is the first day the new rate applies; entered is the month HR keyed it.
PEOPLE: list[dict] = [
    {
        "employee_number": "NL-US-001",
        "legal_name": "Maya Chen",
        "entity_code": "US",
        "department": "Engineering",
        "hire_date": "2022-04-04",
        "monthly_rate": "15000.00",
        "bank_account": "1002003001",
        "grant": {"number": "GR-US-001", "date": "2024-03-01", "fair_value": "48000.00"},
    },
    {
        "employee_number": "NL-US-002",
        "legal_name": "Jonah Hale",
        "entity_code": "US",
        "department": "Engineering",
        "hire_date": "2023-01-09",
        "monthly_rate": "12000.00",
        "bank_account": "1002003002",
        "grant": {"number": "GR-US-002", "date": "2024-03-01", "fair_value": "24000.00"},
    },
    {
        "employee_number": "NL-US-003",
        "legal_name": "Priya Shah",
        "entity_code": "US",
        "department": "Product",
        "hire_date": "2024-02-01",
        "monthly_rate": "15000.00",
        "termination_date": "2026-03-12",
        "termination_recorded_on": "2026-04-02",
        "bank_account": "1002003003",
        "grant": {"number": "GR-US-003", "date": "2024-03-01", "fair_value": "48000.00"},
    },
    {
        "employee_number": "NL-US-004",
        "legal_name": "Evan Brooks",
        "entity_code": "US",
        "department": "GTM",
        "hire_date": "2023-06-01",
        "monthly_rate": "10000.00",
        "raises": [
            {
                "effective": "2026-04-01",
                "monthly_rate": "12000.00",
                "entered": "2026-06",
                "entered_by": "sam.kerr",
                "approved_by": "lee.park",
            }
        ],
        "bank_account": "1002003004",
        "grant": {"number": "GR-US-004", "date": "2024-03-01", "fair_value": "24000.00"},
    },
    {
        "employee_number": "NL-US-005",
        "legal_name": "Riley Nguyen",
        "entity_code": "US",
        "department": "Engineering",
        "hire_date": "2024-11-01",
        "monthly_rate": "10000.00",
        "bank_account": "1002003005",
        "grant": {"number": "GR-US-005", "date": "2024-11-01", "fair_value": "36000.00"},
    },
    {
        "employee_number": "NL-UK-001",
        "legal_name": "Amira Khan",
        "entity_code": "UK",
        "department": "Engineering",
        "hire_date": "2022-09-01",
        "monthly_rate": "8000.00",
        "bank_account": "2002003001",
        "grant": {"number": "GR-UK-001", "date": "2024-03-01", "fair_value": "48000.00"},
    },
    {
        "employee_number": "NL-UK-002",
        "legal_name": "Owen Blake",
        "entity_code": "UK",
        "department": "GTM",
        "hire_date": "2024-05-01",
        "monthly_rate": "7000.00",
        "bank_account": "2002003002",
        "grant": {"number": "GR-UK-002", "date": "2024-05-01", "fair_value": "24000.00"},
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
        "grant": {"number": "GR-UK-003", "date": "2025-08-01", "fair_value": "24000.00"},
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
        "grant": {"number": "GR-DE-002", "date": "2024-03-01", "fair_value": "24000.00"},
    },
    {
        "employee_number": "NL-DE-003",
        "legal_name": "Greta Hoffmann",
        "entity_code": "DE",
        "department": "G&A",
        "hire_date": "2025-01-06",
        "monthly_rate": "7500.00",
        "bank_account": "3002003003",
        "grant": {"number": "GR-DE-003", "date": "2025-01-06", "fair_value": "24000.00"},
    },
    {
        "employee_number": "NL-US-006",
        "legal_name": "Nadia Rossi",
        "entity_code": "US",
        "department": "Product",
        "hire_date": "2024-06-03",
        "monthly_rate": "11000.00",
        "bank_account": "1002003006",
        "grant": {"number": "GR-US-006", "date": "2024-06-03", "fair_value": "24000.00"},
    },
    {
        "employee_number": "NL-DE-004",
        "legal_name": "Tomas Fischer",
        "entity_code": "DE",
        "department": "Engineering",
        "hire_date": "2024-01-08",
        "monthly_rate": "8500.00",
        "bank_account": "3002003004",
        "transfers": [
            {
                "effective": "2026-06-01",
                "entity_code": "UK",
                "department": "Engineering",
                "bank_account": "2002003004",
            }
        ],
        "grant": {"number": "GR-DE-004", "date": "2024-03-01", "fair_value": "24000.00"},
    },
    {
        "employee_number": "NL-UK-004",
        "legal_name": "Ines Duarte",
        "entity_code": "UK",
        "department": "Product",
        "hire_date": "2026-06-02",
        "monthly_rate": "6200.00",
        "bank_account": "2002003005",
    },
]

# Someone the payroll provider pays who is not in HR. Used by the ghost_on_register plant.
SAM_ORTIZ = {
    "employee_number": "PAY-0099",
    "legal_name": "Sam Ortiz",
    "entity_code": "US",
    "department": "G&A",
    "hire_date": "2026-06-01",
    "monthly_rate": "8000.00",
    "bank_account": "1002003099",
}


# ---------------------------------------------------------------- months


@dataclass(frozen=True)
class Month:
    period: str

    @property
    def year(self) -> int:
        return int(self.period[:4])

    @property
    def number(self) -> int:
        return int(self.period[5:7])

    @property
    def start(self) -> str:
        return f"{self.period}-01"

    @property
    def end(self) -> str:
        return f"{self.period}-{calendar.monthrange(self.year, self.number)[1]:02d}"

    @property
    def label(self) -> str:
        return f"{calendar.month_name[self.number]} {self.year}"

    @property
    def received_on(self) -> str:
        """Drops arrive on the 2nd of the following month."""
        year, month = divmod(self.year * 12 + self.number, 12)
        return f"{year:04d}-{month + 1:02d}-02"


def month_of(period: str) -> Month:
    year_text, _, month_text = period.partition("-")
    if len(year_text) != 4 or len(month_text) != 2 or not 1 <= int(month_text) <= 12:
        raise ValueError(f"Period must be YYYY-MM, got {period!r}")
    return Month(period)


def months_between(first_day: str, later_month: Month) -> int:
    year, month = int(first_day[:4]), int(first_day[5:7])
    return (later_month.year * 12 + later_month.number) - (year * 12 + month)


def add_months(iso_date: str, months: int) -> str:
    year, month, day = (int(part) for part in iso_date.split("-"))
    month_index = year * 12 + (month - 1) + months
    new_year, new_month = divmod(month_index, 12)
    return f"{new_year:04d}-{new_month + 1:02d}-{day:02d}"


def money(amount: Decimal) -> str:
    return f"{amount.quantize(CENT):.2f}"


# ---------------------------------------------------------------- what really happened


def hr_view(person: dict, month: Month) -> tuple[str, str | None, str | None]:
    """HR shows a termination only once HR has recorded it."""
    terminated_on = person.get("termination_date")
    recorded_on = person.get("termination_recorded_on")
    if terminated_on is None or recorded_on is None or recorded_on > month.end:
        return "active", None, None
    return "terminated", terminated_on, recorded_on


def in_hr(person: dict, month: Month) -> bool:
    return person["hire_date"] <= month.end


def on_payroll(person: dict, month: Month) -> bool:
    return in_hr(person, month) and hr_view(person, month)[0] == "active"


def assignment(person: dict, month: Month) -> tuple[str, str, str, str]:
    """Entity, department, bank, and currency as of this month, after any transfer HR has recorded."""
    entity = person["entity_code"]
    department = person["department"]
    bank = person["bank_account"]
    for move in person.get("transfers", []):
        if move["effective"] <= month.end:
            entity = move["entity_code"]
            department = move.get("department", department)
            bank = move.get("bank_account", bank)
    return entity, department, bank, CURRENCIES[entity]


def person_by_number(employee_number: str) -> dict:
    for person in PEOPLE:
        if person["employee_number"] == employee_number:
            return person
    raise KeyError(employee_number)


def inclusive_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def uk_cycle(month: Month) -> tuple[str, str]:
    """UK pay runs from the 26th of the previous month to the 25th of this month."""
    previous = add_months(month.start, -1)
    return f"{previous[:7]}-26", f"{month.period}-25"


def pay_window(person: dict, month: Month) -> tuple[str, str, str, str]:
    entity, _, _, _ = assignment(person, month)
    if entity == "UK":
        full_start, full_end = uk_cycle(month)
    else:
        full_start, full_end = month.start, month.end
    start, end = full_start, full_end
    if person["hire_date"] > start:
        start = person["hire_date"]
    return start, end, full_start, full_end


def _known_raises(person: dict, month: Month) -> list[dict]:
    return [
        item
        for item in person.get("raises", [])
        if item["entered"] <= month.period and item["effective"] <= month.end
    ]


def monthly_rate(person: dict, month: Month) -> Decimal:
    raises = _known_raises(person, month)
    if raises:
        return Decimal(max(raises, key=lambda item: item["effective"])["monthly_rate"])
    return Decimal(person["monthly_rate"])


def retro_pay(person: dict, month: Month) -> tuple[str | None, Decimal | None]:
    """A raise keyed this month but effective earlier is paid as a lump for the missed months."""
    previous = Decimal(person["monthly_rate"])
    for item in sorted(person.get("raises", []), key=lambda raise_: raise_["effective"]):
        rate = Decimal(item["monthly_rate"])
        if item["entered"] == month.period and item["effective"] < month.start:
            missed = months_between(item["effective"], month)
            return item["effective"], (rate - previous) * missed
        previous = rate
    return None, None


def pay_amounts(entity: str, gross: Decimal, contribution_rate: Decimal | None = None) -> dict[str, Decimal]:
    rates = ENTITY_RATES[entity]
    withholding = (gross * rates["withholding"]).quantize(CENT)
    return {
        "gross": gross,
        "withholding": withholding,
        "net": gross - withholding,
        "employer_tax": (gross * rates["employer_tax"]).quantize(CENT),
        "contribution": (gross * (contribution_rate or rates["contribution"])).quantize(CENT),
    }


def set_gross(row: dict, gross: Decimal, contribution_rate: Decimal | None = None) -> None:
    amounts = pay_amounts(row["entity_code"], gross, contribution_rate)
    row["gross_wages"] = money(amounts["gross"])
    row["employee_withholdings"] = money(amounts["withholding"])
    row["net_pay"] = money(amounts["net"])
    row["employer_tax"] = money(amounts["employer_tax"])
    row["employer_contribution"] = money(amounts["contribution"])


def hr_row(person: dict, month: Month) -> dict:
    status, terminated_on, recorded_on = hr_view(person, month)
    entity, department, bank, currency = assignment(person, month)
    return {
        "employee_number": person["employee_number"],
        "legal_name": person["legal_name"],
        "entity_code": entity,
        "department": department,
        "status": status,
        "hire_date": person["hire_date"],
        "termination_date": terminated_on,
        "termination_recorded_on": recorded_on,
        "annual_salary": money(monthly_rate(person, month) * 12),
        "currency": currency,
        "bank_account": bank,
        "bank_routing": "111000025",
    }


def cycle_gross(person: dict, month: Month) -> Decimal:
    start, end, full_start, full_end = pay_window(person, month)
    rate = monthly_rate(person, month)
    if (start, end) != (full_start, full_end):
        return (rate * inclusive_days(start, end) / inclusive_days(full_start, full_end)).quantize(CENT)
    return rate


def payroll_row(person: dict, month: Month) -> dict:
    retro_effective, retro_gross = retro_pay(person, month)
    entity, department, bank, currency = assignment(person, month)
    start, end, _, _ = pay_window(person, month)
    row = {
        "employee_number": person["employee_number"],
        "legal_name": person["legal_name"],
        "entity_code": entity,
        "department": department,
        "pay_period_start": start,
        "pay_period_end": end,
        "currency": currency,
        "destination_account": bank,
        "retro_effective_date": retro_effective,
        "retro_gross": money(retro_gross) if retro_gross is not None else None,
    }
    set_gross(row, cycle_gross(person, month) + (retro_gross or Decimal("0")))
    return {
        key: row[key]
        for key in (
            "employee_number",
            "legal_name",
            "entity_code",
            "department",
            "pay_period_start",
            "pay_period_end",
            "gross_wages",
            "employee_withholdings",
            "net_pay",
            "employer_tax",
            "employer_contribution",
            "currency",
            "destination_account",
            "retro_effective_date",
            "retro_gross",
        )
    }


def has_grant(person: dict, month: Month) -> bool:
    return "grant" in person and person["grant"]["date"] <= month.end


def straight_line(fair_value: Decimal) -> Decimal:
    return (fair_value / VEST_MONTHS).quantize(CENT)


def equity_row(person: dict, month: Month) -> dict:
    """What a correct equity platform would show. Plants make it wrong."""
    grant = person["grant"]
    fair_value = Decimal(grant["fair_value"])
    vest_end = add_months(grant["date"], VEST_MONTHS)
    entity, department, _, currency = assignment(person, month)
    if hr_view(person, month)[0] == "terminated":
        status, expense = "forfeited", Decimal("0")
    elif vest_end <= month.start:
        status, expense = "vested", Decimal("0")
    else:
        status, expense = "vesting", straight_line(fair_value)
    return {
        "grant_number": grant["number"],
        "employee_number": person["employee_number"],
        "legal_name": person["legal_name"],
        "entity_code": entity,
        "department": department,
        "grant_date": grant["date"],
        "cliff_end": add_months(grant["date"], 12),
        "vest_end": vest_end,
        "shares": int(fair_value / Decimal(10)),
        "fair_value": money(fair_value),
        "currency": currency,
        "board_approved": True,
        "board_approval_date": grant["date"],
        "platform_status": status,
        "monthly_expense": money(expense),
    }


# ---------------------------------------------------------------- plants


@dataclass
class Drop:
    """A month while it is being built. Plants edit it in place."""

    month: Month
    hr: list[dict]
    payroll: list[dict]
    equity: list[dict]
    documents: list[dict]
    funding: list[dict] = field(default_factory=list)
    bank: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Plant:
    """One source system getting something wrong. stage is when it applies:
    'sources' edits HR, payroll, equity, or documents; 'funding' edits funding instructions."""

    name: str
    stage: str
    apply: Callable[[Drop], None]


def _one(rows: list[dict], key: str, value: str) -> dict:
    matches = [row for row in rows if row[key] == value]
    if len(matches) != 1:
        raise ValueError(f"Expected one row with {key}={value}, found {len(matches)}")
    return matches[0]


def paid_past_termination(employee_number: str, paid_through: str, gross: str) -> Plant:
    """Payroll keeps paying after the real last day because HR had not recorded the exit yet."""

    def apply(drop: Drop) -> None:
        row = _one(drop.payroll, "employee_number", employee_number)
        row["pay_period_end"] = paid_through
        set_gross(row, Decimal(gross))

    return Plant(f"paid_past_termination:{employee_number}", "sources", apply)


def stale_vesting_flag(*grant_numbers: str) -> Plant:
    """The equity platform still shows a leaver's grant as vesting and keeps expensing it."""

    def apply(drop: Drop) -> None:
        for number in grant_numbers:
            row = _one(drop.equity, "grant_number", number)
            row["platform_status"] = "vesting"
            row["monthly_expense"] = money(straight_line(Decimal(row["fair_value"])))

    return Plant(f"stale_vesting_flag:{','.join(grant_numbers)}", "sources", apply)


def unapproved_grant(grant_number: str) -> Plant:
    """A grant on the equity platform with no board approval behind it."""

    def apply(drop: Drop) -> None:
        row = _one(drop.equity, "grant_number", grant_number)
        row["board_approved"] = False
        row["board_approval_date"] = None

    return Plant(f"unapproved_grant:{grant_number}", "sources", apply)


def contribution_rate_break(employee_number: str, rate: str) -> Plant:
    """The payroll provider applies the wrong employer contribution rate to one person."""

    def apply(drop: Drop) -> None:
        row = _one(drop.payroll, "employee_number", employee_number)
        row["employer_contribution"] = money(Decimal(row["gross_wages"]) * Decimal(rate))

    return Plant(f"contribution_rate_break:{employee_number}", "sources", apply)


def ghost_on_register(person: dict) -> Plant:
    """Someone on the payroll register who does not exist in HR."""

    def apply(drop: Drop) -> None:
        drop.payroll.append(payroll_row(person, drop.month))

    return Plant(f"ghost_on_register:{person['employee_number']}", "sources", apply)


def email(title: str, lines: list[str]) -> Plant:
    """An email that lands in the payroll inbox. Text keeps its typos."""

    def apply(drop: Drop) -> None:
        drop.documents.append({"system": "email", "title": title, "body": "\n".join(lines)})

    return Plant(f"email:{title}", "sources", apply)


def uncleared_funding(*payment_ids: str) -> Plant:
    """Funding lines the bank has not shown as paid."""

    def apply(drop: Drop) -> None:
        for payment_id in payment_ids:
            _one(drop.funding, "payment_id", payment_id)["cleared"] = False

    return Plant(f"uncleared_funding:{','.join(payment_ids)}", "funding", apply)


def diverted_funding(employee_number: str, account: str) -> Plant:
    """The provider sent net pay to an account from an email, and the bank has not cleared it."""

    def apply(drop: Drop) -> None:
        for row in drop.funding:
            if row["employee_number"] == employee_number:
                row["destination_account"] = account
                row["cleared"] = False

    return Plant(f"diverted_funding:{employee_number}", "funding", apply)


def vesting_typo(grant_number: str, vest_end: str) -> Plant:
    """The equity platform has the wrong vest end date. The signed grant is right."""

    def apply(drop: Drop) -> None:
        _one(drop.equity, "grant_number", grant_number)["vest_end"] = vest_end

    return Plant(f"vesting_typo:{grant_number}", "sources", apply)


def duplicate_grant(grant_number: str, alias: str) -> Plant:
    """The same grant appears twice on the equity platform."""

    def apply(drop: Drop) -> None:
        copy = dict(_one(drop.equity, "grant_number", grant_number))
        copy["grant_number"] = alias
        drop.equity.append(copy)

    return Plant(f"duplicate_grant:{grant_number}", "sources", apply)


def stale_home_entity(employee_number: str) -> Plant:
    """Payroll and equity still sit in the old entity after HR recorded a transfer."""

    def apply(drop: Drop) -> None:
        person = person_by_number(employee_number)
        home = person["entity_code"]
        for row in (drop.payroll, drop.equity):
            item = _one(row, "employee_number", employee_number)
            item["entity_code"] = home
            item["department"] = person["department"]
            item["currency"] = CURRENCIES[home]
        pay = _one(drop.payroll, "employee_number", employee_number)
        pay["pay_period_start"] = drop.month.start
        pay["pay_period_end"] = drop.month.end
        pay["destination_account"] = person["bank_account"]
        set_gross(pay, Decimal(person["monthly_rate"]))

    return Plant(f"stale_home_entity:{employee_number}", "sources", apply)


def duplicate_hr_row(employee_number: str) -> Plant:
    """A harmless extra copy of an HR row. Same person, same facts."""

    def apply(drop: Drop) -> None:
        drop.hr.append(dict(_one(drop.hr, "employee_number", employee_number)))

    return Plant(f"duplicate_hr_row:{employee_number}", "sources", apply)


def late_bank_posting(payment_id: str, posted_on: str) -> Plant:
    """A bank line that posted one day after month-end. Mess, not an unpaid wage."""

    def apply(drop: Drop) -> None:
        _one(drop.bank, "payment_id", payment_id)["posted_on"] = posted_on

    return Plant(f"late_bank_posting:{payment_id}", "bank", apply)


JONAH_EMAIL = email(
    "Jonah Hale bank change email",
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
    ],
)
JONAH_REJECTION = email(
    "Controller rejection of Jonah Hale bank change",
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
    ],
)
MAYA_EMAIL = email(
    "Maya Chen bank change email",
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
    ],
)

# Months the test world ships with, and what goes wrong in each.
MONTHS: dict[str, dict] = {
    "2026-03": {"closed": True},
    "2026-06": {"closed": False},
}
DEFAULT_PLANTS: dict[str, list[Plant]] = {
    "2026-03": [
        paid_past_termination("NL-US-003", paid_through="2026-03-15", gross="7500.00"),
        stale_vesting_flag("GR-UK-003"),
        unapproved_grant("GR-DE-001"),
        contribution_rate_break("NL-UK-001", "0.03"),
        vesting_typo("GR-US-002", "2027-03-01"),
        JONAH_EMAIL,
        JONAH_REJECTION,
    ],
    "2026-06": [
        stale_vesting_flag("GR-US-003", "GR-UK-003"),
        unapproved_grant("GR-DE-001"),
        contribution_rate_break("NL-UK-001", "0.03"),
        ghost_on_register(SAM_ORTIZ),
        vesting_typo("GR-US-002", "2027-03-01"),
        duplicate_grant("GR-US-006", "GR-US-006-DUP"),
        stale_home_entity("NL-DE-004"),
        duplicate_hr_row("NL-DE-003"),
        MAYA_EMAIL,
        diverted_funding("NL-US-001", "9911882277"),
        uncleared_funding("PAY-2026-06-NL-US-002", "PAY-2026-06-NL-US-005"),
        late_bank_posting("PAY-2026-06-NL-DE-002", "2026-07-01"),
    ],
}


# ---------------------------------------------------------------- derived records


def funding_rows(row: dict, month: Month) -> list[dict]:
    """Net pay instructions from one register row. A retro lump is paid as its own line."""
    base = {
        "employee_number": row["employee_number"],
        "legal_name": row["legal_name"],
        "entity_code": row["entity_code"],
        "currency": row["currency"],
        "destination_account": row["destination_account"],
        "cleared": True,
    }
    payment_id = f"PAY-{month.period}-{row['employee_number']}"
    net = Decimal(row["net_pay"])
    if row["retro_gross"] is None:
        return [{"payment_id": payment_id, **base, "amount": money(net), "kind": "net_pay"}]
    keep = Decimal("1") - ENTITY_RATES[row["entity_code"]]["withholding"]
    regular_net = ((Decimal(row["gross_wages"]) - Decimal(row["retro_gross"])) * keep).quantize(CENT)
    return [
        {"payment_id": payment_id, **base, "amount": money(regular_net), "kind": "net_pay"},
        {"payment_id": f"{payment_id}-RETRO", **base, "amount": money(net - regular_net), "kind": "retro_net_pay"},
    ]


def _instruction(row: dict) -> dict:
    return {
        key: row[key]
        for key in (
            "payment_id",
            "employee_number",
            "legal_name",
            "entity_code",
            "amount",
            "currency",
            "destination_account",
            "kind",
            "cleared",
        )
    }


def debit(account: str, amount: Decimal, department: str | None) -> dict:
    return {"account_code": account, "department": department, "debit": money(amount), "credit": "0.00"}


def credit(account: str, amount: Decimal) -> dict:
    return {"account_code": account, "department": None, "debit": "0.00", "credit": money(amount)}


def balanced(entry: dict) -> dict:
    debits = sum((Decimal(line["debit"]) for line in entry["lines"]), Decimal("0.00"))
    credits = sum((Decimal(line["credit"]) for line in entry["lines"]), Decimal("0.00"))
    if debits != credits or debits <= 0 or len(entry["lines"]) < 2:
        raise RuntimeError(f"{entry['memo']} does not balance: {debits} vs {credits}")
    return entry


def payroll_entry(entity: str, month: Month, rows: list[dict]) -> dict | None:
    if not rows:
        return None
    buckets: dict[tuple[str, str], Decimal] = {}
    nets = withholdings = taxes = contributions = Decimal("0.00")
    for row in rows:
        department = row["department"]
        for account, field_name in (
            ("5000", "gross_wages"),
            ("5100", "employer_tax"),
            ("5110", "employer_contribution"),
        ):
            amount = Decimal(row[field_name])
            if amount == 0:
                continue
            buckets[(account, department)] = buckets.get((account, department), Decimal("0.00")) + amount
        nets += Decimal(row["net_pay"])
        withholdings += Decimal(row["employee_withholdings"])
        taxes += Decimal(row["employer_tax"])
        contributions += Decimal(row["employer_contribution"])
    lines = [debit(account, amount, department) for (account, department), amount in sorted(buckets.items())]
    lines.append(credit("2100", nets))
    lines.append(credit("2200", withholdings))
    if taxes:
        lines.append(credit("2210", taxes))
    if contributions:
        lines.append(credit("2220", contributions))
    return balanced(
        {
            "entity_code": entity,
            "kind": "payroll",
            "memo": f"{entity} payroll for {month.label}, as already booked",
            "lines": lines,
        }
    )


def equity_entry(entity: str, month: Month, rows: list[dict]) -> dict | None:
    """Stock expense as the platform fed it to the ledger. Zero lines are left out."""
    buckets: dict[str, Decimal] = {}
    for row in rows:
        amount = Decimal(row["monthly_expense"])
        if amount:
            buckets[row["department"]] = buckets.get(row["department"], Decimal("0.00")) + amount
    if not buckets:
        return None
    lines = [debit("5200", amount, department) for department, amount in sorted(buckets.items())]
    lines.append(credit("3000", sum(buckets.values(), Decimal("0.00"))))
    return balanced(
        {
            "entity_code": entity,
            "kind": "equity",
            "memo": f"{entity} stock compensation for {month.label}, fed by the equity platform",
            "lines": lines,
        }
    )


def bank_entry(entity: str, month: Month, instructions: list[dict]) -> dict | None:
    if not instructions:
        return None
    total = sum((Decimal(row["amount"]) for row in instructions), Decimal("0.00"))
    return balanced(
        {
            "entity_code": entity,
            "kind": "bank",
            "memo": f"{entity} net pay cleared in {month.label}, per bank",
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


# ---------------------------------------------------------------- build


def build_drop(period: str, plants: list[Plant] | None = None, closed: bool | None = None) -> dict:
    """Build one month's file drop.

    plants defaults to the scenario the test world ships with for that month, or none.
    closed says whether the month was already booked and locked when the drop arrived.
    """
    month = month_of(period)
    if plants is None:
        plants = DEFAULT_PLANTS.get(period, [])
    if closed is None:
        closed = MONTHS.get(period, {}).get("closed", False)
    unknown = [plant.stage for plant in plants if plant.stage not in ("sources", "funding", "bank")]
    if unknown:
        raise ValueError(f"Unknown plant stage {unknown[0]!r}")

    drop = Drop(
        month=month,
        hr=[hr_row(person, month) for person in PEOPLE if in_hr(person, month)],
        payroll=[payroll_row(person, month) for person in PEOPLE if on_payroll(person, month)],
        equity=[equity_row(person, month) for person in PEOPLE if has_grant(person, month)],
        documents=[{"system": "rate_card", "title": "Employer contribution rate card", "body": rate_card_body()}],
    )
    for plant in plants:
        if plant.stage == "sources":
            plant.apply(drop)
    drop.funding = [line for row in drop.payroll for line in funding_rows(row, month)]
    for plant in plants:
        if plant.stage == "funding":
            plant.apply(drop)

    funding = [_instruction(row) for row in drop.funding]
    cleared = [row for row in funding if row["cleared"]]
    drop.bank = [
        {
            "entity_code": row["entity_code"],
            "posted_on": month.end,
            "amount": row["amount"],
            "currency": row["currency"],
            "payment_id": row["payment_id"],
            "description": f"Net pay {row['legal_name']} {row['payment_id']}",
            "destination_account": row["destination_account"],
        }
        for row in cleared
    ]
    for plant in plants:
        if plant.stage == "bank":
            plant.apply(drop)
    ledger_entries = []
    for entity in ENTITIES:
        entries = []
        if closed:
            entries.append(payroll_entry(entity, month, [row for row in drop.payroll if row["entity_code"] == entity]))
            entries.append(bank_entry(entity, month, [row for row in cleared if row["entity_code"] == entity]))
        entries.append(equity_entry(entity, month, [row for row in drop.equity if row["entity_code"] == entity]))
        ledger_entries.extend(entry for entry in entries if entry is not None)

    return {
        "period": period,
        "description": "Synthetic Northlight file drop. People, account numbers, and amounts are fictional.",
        "received_on": month.received_on,
        "close_period": closed,
        "plants": [plant.name for plant in plants],
        "rate_card": RATE_CARD,
        "hr": drop.hr,
        "payroll": drop.payroll,
        "funding": funding,
        "equity": drop.equity,
        "bank": drop.bank,
        "documents": drop.documents,
        "ledger_entries": ledger_entries,
    }


def canonical(drop: dict) -> str:
    return json.dumps(drop, indent=2) + "\n"


def fixture_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "world"


def grants_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "signed_grants"


def actions_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "comp_actions"


def signed_grant_text(person: dict) -> str:
    grant = person["grant"]
    approved = grant.get("board_approved", True)
    lines = [
        f"Northlight Labs signed grant {grant['number']}",
        f"Employee: {person['legal_name']} ({person['employee_number']})",
        f"Home entity: {person['entity_code']} {person['department']}",
        f"Grant date: {grant['date']}",
        f"Cliff end: {add_months(grant['date'], 12)}",
        f"Vest end: {add_months(grant['date'], VEST_MONTHS)}",
        f"Shares: {int(Decimal(grant['fair_value']) / Decimal(10))}",
        f"Grant-date fair value: {grant['fair_value']} {CURRENCIES[person['entity_code']]}",
        f"Service period: {VEST_MONTHS} months, straight-line.",
        f"Board approved: {'yes, ' + grant['date'] if approved else 'no'}",
        "",
    ]
    return "\n".join(lines)


def write_signed_grants() -> None:
    directory = grants_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for person in PEOPLE:
        if "grant" not in person:
            continue
        path = directory / f"{person['grant']['number']}.txt"
        path.write_text(signed_grant_text(person), encoding="utf-8", newline="\n")


def write_comp_actions() -> None:
    directory = actions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    actions = []
    for person in PEOPLE:
        for item in person.get("raises", []):
            actions.append(
                {
                    "action_id": f"COMP-{person['employee_number']}-{item['effective']}",
                    "employee_number": person["employee_number"],
                    "legal_name": person["legal_name"],
                    "kind": "salary_change",
                    "effective": item["effective"],
                    "entered": item["entered"],
                    "entered_by": item["entered_by"],
                    "approved_by": item["approved_by"],
                    "previous_monthly_rate": person["monthly_rate"],
                    "monthly_rate": item["monthly_rate"],
                    "currency": CURRENCIES[person["entity_code"]],
                }
            )
    path = directory / "2026.json"
    path.write_text(json.dumps(actions, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_fixtures() -> None:
    directory = fixture_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for period in MONTHS:
        path = directory / f"{period}.json"
        path.write_text(canonical(build_drop(period)), encoding="utf-8", newline="\n")
    write_signed_grants()
    write_comp_actions()


if __name__ == "__main__":
    write_fixtures()
