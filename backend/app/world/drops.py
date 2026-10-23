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

import copy
import functools
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.periods import Month, add_months, month_of, months_between

CENT = Decimal("0.01")
VEST_MONTHS = 48
ENTITIES = ("US", "UK", "DE")
CURRENCIES = {"US": "USD", "UK": "GBP", "DE": "EUR"}
RATE_CARD = {
    "us_401k_match_rate": "0.04",
    "uk_pension_rate": "0.05",
    "de_social_rate": "0.20",
    "fx_band": "0.01",
}
# Real month-end rates from Open Exchange Rates, recorded once (D20). The management pack, the rate
# Northlight's old process used, sits 0.4% under the reference: inside the 1% band, so explained.
FX_RECORDED = Path(__file__).resolve().parents[2] / "fixtures" / "fx"
MANAGEMENT_PACK_FACTOR = Decimal("0.996")


def platform_rates(period: str) -> dict[str, Decimal]:
    """USD per unit as the simulated equity platform translates: the month-end rate, or the latest it had before.
    The platform's choice, not Vestly's; Vestly books only at the month's own rate."""
    end = month_of(period).end
    candidates = sorted(path for path in FX_RECORDED.glob("*.json") if path.stem <= end)
    if not candidates:
        raise ValueError(f"No recorded exchange rate on or before {end}.")
    rates = json.loads(candidates[-1].read_text(encoding="utf-8"))["rates"]
    return {"USD": Decimal("1"), **{code: (Decimal(1) / Decimal(str(rates[code]))).quantize(Decimal("0.000001")) for code in ("GBP", "EUR")}}


def rate_card(period: str) -> dict:
    """The drop's rate card for a month: contribution rates, and the month-end rates if that day's rate exists."""
    card = dict(RATE_CARD)
    path = FX_RECORDED / f"{month_of(period).end}.json"
    if path.exists():
        rates = json.loads(path.read_text(encoding="utf-8"))["rates"]
        gbp = (Decimal(1) / Decimal(str(rates["GBP"]))).quantize(Decimal("0.000001"))
        eur = (Decimal(1) / Decimal(str(rates["EUR"]))).quantize(Decimal("0.000001"))
        card["gbp_usd_reference"] = str(gbp)
        card["eur_usd_reference"] = str(eur)
        card["gbp_usd_reporting"] = str((gbp * MANAGEMENT_PACK_FACTOR).quantize(Decimal("0.0001")))
    return card
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


def final_pay_due(person: dict, month: Month) -> bool:
    """A leaver marked final_pay is paid for the days up to their last day in the cycle that holds it."""
    terminated_on = person.get("termination_date")
    if not person.get("final_pay") or terminated_on is None:
        return False
    _start, _end, full_start, full_end = pay_window(person, month)
    return full_start <= terminated_on <= full_end and person["hire_date"] <= terminated_on


def on_payroll(person: dict, month: Month) -> bool:
    return in_hr(person, month) and (hr_view(person, month)[0] == "active" or final_pay_due(person, month))


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
    terminated_on = person.get("termination_date")
    if person.get("final_pay") and terminated_on is not None and full_start <= terminated_on < end:
        end = terminated_on
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
    if hr_view(person, month)[0] == "terminated" and person["termination_date"][:7] != month.period:
        # The platform expenses the leaver's final month, like the subledger, and forfeits from the next.
        status, expense = "forfeited", Decimal("0")
    elif vest_end[:7] <= month.period:
        # Vested out from the month of the vest end: 48 monthly expenses, not a 49th (as the subledger rules).
        status, expense = "vested", Decimal("0")
    else:
        status, expense = "vesting", straight_line(fair_value)
    grant_currency = CURRENCIES[person["entity_code"]]
    if currency != grant_currency:
        # After a transfer the platform books in the new entity's currency, at the reference rates.
        to_usd = platform_rates(month.period)
        rate = (to_usd[grant_currency] / to_usd[currency]).quantize(Decimal("0.000001"))
        expense = (expense * rate).quantize(CENT)
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
    people: list[dict] = field(default_factory=list)
    funding: list[dict] = field(default_factory=list)
    bank: list[dict] = field(default_factory=list)
    ledger_entries: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Plant:
    """One source system getting something wrong. stage is when it applies:
    'sources' edits HR, payroll, equity, or documents; 'funding' edits funding instructions;
    'bank' edits bank lines; 'ledger' edits what the old process booked."""

    name: str
    stage: str
    apply: Callable[[Drop], None]


def _person(drop: Drop, employee_number: str) -> dict:
    for person in drop.people:
        if person["employee_number"] == employee_number:
            return person
    raise KeyError(employee_number)


def _one(rows: list[dict], key: str, value: str) -> dict:
    matches = [row for row in rows if row[key] == value]
    if len(matches) != 1:
        raise ValueError(f"Expected one row with {key}={value}, found {len(matches)}")
    return matches[0]


def paid_past_termination(employee_number: str, paid_through: str) -> Plant:
    """Payroll keeps paying after the real last day because HR had not recorded the exit yet.

    The register pays the days from the cycle start through paid_through, at the person's rate.
    """

    def apply(drop: Drop) -> None:
        row = _one(drop.payroll, "employee_number", employee_number)
        row["pay_period_end"] = paid_through
        rate = monthly_rate(_person(drop, employee_number), drop.month)
        worked = inclusive_days(row["pay_period_start"], paid_through)
        full = inclusive_days(*pay_window(_person(drop, employee_number), drop.month)[2:])
        set_gross(row, (rate * worked / full).quantize(CENT))

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
        person = _person(drop, employee_number)
        home = person["entity_code"]
        for row in (drop.payroll, drop.equity):
            item = _one(row, "employee_number", employee_number)
            item["entity_code"] = home
            item["department"] = person["department"]
            item["currency"] = CURRENCIES[home]
        grant = _one(drop.equity, "employee_number", employee_number)
        grant["monthly_expense"] = money(straight_line(Decimal(grant["fair_value"])))
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


def funding_booked_in_full(entity: str) -> Plant:
    """Treasury books the whole payment file as paid, though the bank has not cleared every line."""

    def apply(drop: Drop) -> None:
        lines = [row for row in drop.funding if row["entity_code"] == entity]
        total = sum((Decimal(row["amount"]) for row in lines), Decimal("0.00"))
        drop.ledger_entries.append(
            balanced(
                {
                    "entity_code": entity,
                    "kind": "bank",
                    "memo": f"{entity} net pay for {drop.month.label}, booked by treasury from the payment file",
                    "lines": [debit("2100", total, None), credit("1000", total)],
                }
            )
        )

    return Plant(f"funding_booked_in_full:{entity}", "ledger", apply)


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
# January to May were booked and locked by Northlight's old process before Vestly arrived.
# June is Vestly's first live close.
MONTHS: dict[str, dict] = {
    "2026-01": {"closed": True},
    "2026-02": {"closed": True},
    "2026-03": {"closed": True},
    "2026-04": {"closed": True},
    "2026-05": {"closed": True},
    "2026-06": {"closed": False},
}


def carry_over(*stale_grants: str) -> list[Plant]:
    """Errors the source systems repeat every month until someone fixes them."""
    plants = [stale_vesting_flag(*stale_grants)] if stale_grants else []
    return plants + [
        unapproved_grant("GR-DE-001"),
        contribution_rate_break("NL-UK-001", "0.03"),
        vesting_typo("GR-US-002", "2027-03-01"),
    ]


DEFAULT_PLANTS: dict[str, list[Plant]] = {
    "2026-01": carry_over("GR-UK-003"),
    "2026-02": carry_over("GR-UK-003"),
    "2026-04": carry_over("GR-US-003", "GR-UK-003"),
    "2026-05": carry_over("GR-US-003", "GR-UK-003"),
    "2026-03": [
        paid_past_termination("NL-US-003", paid_through="2026-03-15"),
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
        uncleared_funding("PAY-2026-06-NL-DE-001"),
        funding_booked_in_full("DE"),
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
            "Reference rates: Open Exchange Rates, end of day on the month's last day (pay day).",
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
    return _build(month, PEOPLE, plants, closed)


def _build(month: Month, people: list[dict], plants: list[Plant], closed: bool) -> dict:
    period = month.period
    unknown = [plant.stage for plant in plants if plant.stage not in ("sources", "funding", "bank", "ledger")]
    if unknown:
        raise ValueError(f"Unknown plant stage {unknown[0]!r}")

    drop = Drop(
        month=month,
        hr=[hr_row(person, month) for person in people if in_hr(person, month)],
        payroll=[payroll_row(person, month) for person in people if on_payroll(person, month)],
        equity=[equity_row(person, month) for person in people if has_grant(person, month)],
        documents=[{"system": "rate_card", "title": "Employer contribution rate card", "body": rate_card_body()}],
        people=people,
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
    drop.ledger_entries = ledger_entries
    for plant in plants:
        if plant.stage == "ledger":
            plant.apply(drop)

    return {
        "period": period,
        "description": "Synthetic Northlight file drop. People, account numbers, and amounts are fictional.",
        "received_on": month.received_on,
        "close_period": closed,
        "plants": [plant.name for plant in plants],
        "rate_card": rate_card(period),
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


def comp_actions(people: list[dict]) -> list[dict]:
    """Approved salary changes. Each raise's previous rate is the rate before it; an unapproved raise is not here."""
    actions = []
    for person in people:
        previous = person["monthly_rate"]
        for item in sorted(person.get("raises", []), key=lambda raise_: raise_["effective"]):
            if not item.get("unapproved"):
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
                        "previous_monthly_rate": previous,
                        "monthly_rate": item["monthly_rate"],
                        "currency": CURRENCIES[person["entity_code"]],
                    }
                )
            previous = item["monthly_rate"]
    return actions


def write_comp_actions() -> None:
    directory = actions_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "2026.json"
    path.write_text(json.dumps(comp_actions(PEOPLE), indent=2) + "\n", encoding="utf-8", newline="\n")


def write_fixtures() -> None:
    directory = fixture_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for period in MONTHS:
        path = directory / f"{period}.json"
        path.write_text(canonical(build_drop(period)), encoding="utf-8", newline="\n")
    write_signed_grants()
    write_comp_actions()
    company_manifest_path().parent.mkdir(parents=True, exist_ok=True)
    company_manifest_path().write_text(json.dumps(company_manifest(), indent=2) + "\n", encoding="utf-8", newline="\n")


# ================================================================ the whole company (D21, D22)
#
# About 300 people across the three entities and four departments, hired from 2018, with monthly
# drops from January 2022 to October 2026: January 2022 to September 2026 arrive closed (booked by
# the old process), October 2026 is the open month. The cast is re-timed so the scored cases happen
# in October; seven named background people carry errors only four years of history can show.
# Seeded, so the same company comes out every time.

COMPANY_FIRST = "2022-01"
COMPANY_OPEN = "2026-10"
# November and December are held out: built now, received only when their dates come (D27).
HELD_OUT = ("2026-11", "2026-12")
COMPANY_LAST = HELD_OUT[-1]
COMPANY_CLOCK_START = "2026-11-02"
COMPANY_SEED = 20260101
HEADCOUNT = {"US": 140, "UK": 85, "DE": 60}
DEPARTMENT_WEIGHTS = (("Engineering", 40), ("Product", 20), ("GTM", 25), ("G&A", 15))
PAY_BANDS = {"US": (7000, 19000), "UK": (4500, 11000), "DE": (5000, 12000)}
HIRE_YEARS = ((2018, 10), (2019, 12), (2020, 13), (2021, 20), (2022, 15), (2023, 12), (2024, 10), (2025, 6), (2026, 2))
FIRST_NAMES = (
    "Aiden", "Beatrice", "Caleb", "Daria", "Elias", "Fatima", "Gabriel", "Hana", "Isaac", "Julia", "Kofi", "Leah",
    "Mateo", "Nora", "Oscar", "Paula", "Quentin", "Rosa", "Samir", "Tara", "Umar", "Vera", "Wesley", "Ximena",
    "Yusuf", "Zoe", "Anton", "Bianca", "Cyrus", "Delia", "Emil", "Freja", "Goran", "Helga", "Ivo", "Jana", "Karim",
    "Lena", "Milo", "Nadia", "Otto", "Priyanka", "Rafael", "Selin", "Tobias", "Ulla", "Viktor", "Wanda", "Yara",
)
LAST_NAMES = (
    "Abbott", "Bauer", "Castro", "Dimitrov", "Ellis", "Fontaine", "Garcia", "Huber", "Ibrahim", "Jensen", "Kowalski",
    "Lambert", "Moreno", "Novak", "Okafor", "Petersen", "Quinn", "Richter", "Sato", "Tanaka", "Unger", "Vogel",
    "Walsh", "Xu", "Young", "Zimmer", "Adeyemi", "Brennan", "Cho", "Dubois", "Eriksen", "Fischer-Lang", "Grant",
    "Haddad", "Iversen", "Jovanovic", "Klein", "Lindgren", "Mensah", "Nakamura", "Olsen", "Park", "Rossi-Bell",
    "Schmidt", "Torres", "Ueda", "Varga", "Weber-Stein", "Yilmaz",
)
COMPANY_SAM = {**SAM_ORTIZ, "hire_date": "2026-10-01"}

# Seven named background people. Each carries one error that only the history shows.
HISTORY_PEOPLE: list[dict] = [
    {   # H4: left in June 2024 after the cliff; the platform kept expensing the 2022 grant until its vest end.
        "employee_number": "NL-US-241", "legal_name": "Victor Lindqvist", "entity_code": "US", "department": "Engineering",
        "hire_date": "2021-09-13", "monthly_rate": "14500.00", "bank_account": "1004002410",
        "termination_date": "2024-06-14", "termination_recorded_on": "2024-06-14", "final_pay": True,
        "grant": {"number": "GR-US-241", "date": "2022-03-01", "fair_value": "48000.00"},
    },
    {   # J1: left in May 2023, then paid again in July 2026.
        "employee_number": "NL-US-242", "legal_name": "Marcus Bell", "entity_code": "US", "department": "GTM",
        "hire_date": "2020-02-03", "monthly_rate": "9800.00", "bank_account": "1004002420",
        "termination_date": "2023-05-19", "termination_recorded_on": "2023-05-19", "final_pay": True,
        "grant": {"number": "GR-US-242", "date": "2020-02-03", "fair_value": "28800.00"},
    },
    {   # A1: hired in August 2026 with the bank account Marcus Bell was paid to until 2023.
        "employee_number": "NL-US-243", "legal_name": "Elena Petrova", "entity_code": "US", "department": "Product",
        "hire_date": "2026-08-03", "monthly_rate": "11200.00", "bank_account": "1004002420",
    },
    {   # S1: an approved raise in 2024, then a raise HR keyed in 2025 that nobody approved.
        "employee_number": "NL-US-244", "legal_name": "Chloe Martin", "entity_code": "US", "department": "GTM",
        "hire_date": "2021-04-05", "monthly_rate": "9000.00", "bank_account": "1004002440",
        "raises": [
            {"effective": "2024-04-01", "monthly_rate": "9450.00", "entered": "2024-03", "entered_by": "sam.kerr", "approved_by": "lee.park"},
            {"effective": "2025-03-01", "monthly_rate": "10400.00", "entered": "2025-02", "entered_by": "sam.kerr", "approved_by": None, "unapproved": True},
        ],
        "grant": {"number": "GR-US-244", "date": "2021-04-05", "fair_value": "33600.00"},
    },
    {   # S2: GTM in HR, coded to Engineering on the register from September 2026.
        "employee_number": "NL-US-245", "legal_name": "Diego Alvarez", "entity_code": "US", "department": "GTM",
        "hire_date": "2022-08-01", "monthly_rate": "8800.00", "bank_account": "1004002450",
        "grant": {"number": "GR-US-245", "date": "2022-08-01", "fair_value": "28800.00"},
    },
    {   # A2: August 2026 net pay sent twice.
        "employee_number": "NL-DE-161", "legal_name": "Jonas Brandt", "entity_code": "DE", "department": "Engineering",
        "hire_date": "2022-05-02", "monthly_rate": "7400.00", "bank_account": "3004001610",
        "grant": {"number": "GR-DE-161", "date": "2022-05-02", "fair_value": "28800.00"},
    },
    {   # M1: hired in September 2026; the provider left him off the September register, so he was not paid.
        "employee_number": "NL-US-246", "legal_name": "Hugo Laurent", "entity_code": "US", "department": "Engineering",
        "hire_date": "2026-09-08", "monthly_rate": "12500.00", "bank_account": "1004002460",
    },
    {   # J2: July 2026 employer pension at 3% instead of 5%, in a month already closed.
        "employee_number": "NL-UK-186", "legal_name": "Freya Lawson", "entity_code": "UK", "department": "Engineering",
        "hire_date": "2021-11-01", "monthly_rate": "7200.00", "bank_account": "2004001860",
        "grant": {"number": "GR-UK-186", "date": "2021-11-01", "fair_value": "28800.00"},
    },
]


def company_periods() -> list[str]:
    periods, current = [], COMPANY_FIRST
    while current <= COMPANY_OPEN:
        periods.append(current)
        current = add_months(f"{current}-01", 1)[:7]
    return periods


def company_cast() -> list[dict]:
    """The cast, re-timed so the scored cases happen in October 2026 (D21)."""
    people = copy.deepcopy(PEOPLE)
    by_number = {person["employee_number"]: person for person in people}
    by_number["NL-US-004"]["raises"] = [{**by_number["NL-US-004"]["raises"][0], "effective": "2026-08-01", "entered": "2026-10"}]
    by_number["NL-DE-004"]["transfers"] = [{**by_number["NL-DE-004"]["transfers"][0], "effective": "2026-10-01"}]
    by_number["NL-UK-004"]["hire_date"] = "2026-10-02"
    return people


def _pick(rng: random.Random, weighted) -> object:
    total = sum(weight for _value, weight in weighted)
    roll = rng.uniform(0, total)
    for value, weight in weighted:
        roll -= weight
        if roll <= 0:
            return value
    return weighted[-1][0]


def _round50(amount: float) -> str:
    return f"{int(round(amount / 50.0)) * 50}.00"


def _background(rng: random.Random, taken_names: set[str], taken_accounts: set[str]) -> list[dict]:
    people = []
    digit = {"US": "1", "UK": "2", "DE": "3"}
    for entity, count in HEADCOUNT.items():
        for index in range(count):
            number = f"NL-{entity}-{101 + index:03d}"
            while True:
                name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
                if name not in taken_names:
                    taken_names.add(name)
                    break
            year = _pick(rng, HIRE_YEARS)
            month = rng.randint(1, 8 if year == 2026 else 12)
            hire = date(year, month, rng.randint(1, 25)).isoformat()
            low, high = PAY_BANDS[entity]
            rate = Decimal(_round50(rng.uniform(low, high)))
            while True:
                account = f"{digit[entity]}0{rng.randrange(10**7, 10**8)}"
                if account not in taken_accounts:
                    taken_accounts.add(account)
                    break
            person = {
                "employee_number": number,
                "legal_name": name,
                "entity_code": entity,
                "department": _pick(rng, DEPARTMENT_WEIGHTS),
                "hire_date": hire,
                "monthly_rate": f"{rate:.2f}",
                "bank_account": account,
            }
            # Raises every April after a first full year, approved by HR ops, keyed the month before.
            raises, current = [], rate
            for raise_year in range(max(int(hire[:4]) + 1, 2022), 2027):
                if f"{raise_year}-04-01" <= add_months(hire, 10) or rng.random() > 0.75:
                    continue
                current = Decimal(_round50(float(current) * rng.uniform(1.03, 1.06)))
                raises.append({
                    "effective": f"{raise_year}-04-01", "monthly_rate": f"{current:.2f}", "entered": f"{raise_year}-03",
                    "entered_by": "sam.kerr", "approved_by": "lee.park",
                })
            if raises:
                person["raises"] = raises
            # Some leave, always after their first year and by September 2026, with a prorated final pay.
            earliest = add_months(hire, 13)
            if rng.random() < 0.13 and earliest <= "2026-09-01":
                first, last = date.fromisoformat(earliest), date(2026, 9, 1)
                day = first + (last - first) * rng.random()
                person["termination_date"] = date(day.year, day.month, min(day.day, 25)).isoformat()
                person["termination_recorded_on"] = person["termination_date"]
                person["final_pay"] = True
                person["raises"] = [item for item in raises if item["effective"] <= person["termination_date"]]
            # Most get a grant at hire, four-year vesting, board approved.
            if rng.random() < 0.8:
                person["grant"] = {"number": f"GR-{entity}-{101 + index:03d}", "date": hire, "fair_value": f"{rng.randint(5, 20) * 4800}.00"}
            people.append(person)
    # A few moved between entities in 2023-2025; payroll, the platform and HR all followed.
    movers = [person for person in people if "termination_date" not in person and person["hire_date"] < "2022-06-01"][:3]
    targets = {"US": "UK", "UK": "DE", "DE": "US"}
    for person, month in zip(movers, ("2023-07-01", "2024-02-01", "2025-05-01")):
        target = targets[person["entity_code"]]
        while True:
            account = f"{digit[target]}0{rng.randrange(10**7, 10**8)}"
            if account not in taken_accounts:
                taken_accounts.add(account)
                break
        person["transfers"] = [{"effective": month, "entity_code": target, "department": person["department"], "bank_account": account}]
    return people


@functools.cache
def _company_people() -> tuple[dict, ...]:
    rng = random.Random(COMPANY_SEED)
    cast = company_cast()
    taken_names = {person["legal_name"] for person in cast + HISTORY_PEOPLE} | {COMPANY_SAM["legal_name"]}
    taken_accounts = {person["bank_account"] for person in cast + HISTORY_PEOPLE} | {"9911882277", "9911002002"}
    people = cast + copy.deepcopy(HISTORY_PEOPLE) + _background(rng, taken_names, taken_accounts)
    # Life after October, applied after the draw so the seeded history stays exactly as it was.
    for person in people:
        person.update(HELD_OUT_EVENTS.get(person["employee_number"], {}))
    return tuple(people)


# N4: Nora Walsh leaves on 13 November; HR keys it on 3 December, so November pays her whole cycle.
HELD_OUT_EVENTS = {
    "NL-UK-103": {"termination_date": "2026-11-13", "termination_recorded_on": "2026-12-03", "final_pay": True},
}


def company_people() -> list[dict]:
    return copy.deepcopy(list(_company_people()))


def returning_leaver_paid(employee_number: str) -> Plant:
    """Someone HR shows as gone for years appears on the register again, paid to their old account."""

    def apply(drop: Drop) -> None:
        drop.payroll.append(payroll_row(_person(drop, employee_number), drop.month))

    return Plant(f"returning_leaver_paid:{employee_number}", "sources", apply)


def duplicate_payment(payment_id: str) -> Plant:
    """The provider's payment file carries one net-pay line twice, and the bank pays both."""

    def apply(drop: Drop) -> None:
        copy_row = dict(_one(drop.funding, "payment_id", payment_id))
        copy_row["payment_id"] = f"{payment_id}-2"
        drop.funding.append(copy_row)

    return Plant(f"duplicate_payment:{payment_id}", "funding", apply)


def dropped_from_register(employee_number: str) -> Plant:
    """The provider leaves someone HR has hired off the register: they are not paid."""

    def apply(drop: Drop) -> None:
        drop.payroll.remove(_one(drop.payroll, "employee_number", employee_number))

    return Plant(f"dropped_from_register:{employee_number}", "sources", apply)


def department_miscoded(employee_number: str, department: str) -> Plant:
    """The register codes a person's pay to the wrong department; HR is unchanged."""

    def apply(drop: Drop) -> None:
        _one(drop.payroll, "employee_number", employee_number)["department"] = department

    return Plant(f"department_miscoded:{employee_number}", "sources", apply)


MAYA_EMAIL_OCTOBER = email(
    "Maya Chen bank change email",
    [
        "From: maya.chen@northlight.example",
        "To: payrol1@northlight.example",
        "Date: 2026-10-02",
        "Subject: Payr0ll direct deposit update",
        "",
        "Hi payrol team,",
        "Please updte my bank acount before the October run.",
        "New acount: 9911882277",
        "Routng: 222000333",
        "Name: Maya Chen",
        "Thnaks",
        "",
    ],
)


def company_plants(period: str) -> list[Plant]:
    """What goes wrong in each month of the company world."""
    plants: list[Plant] = []
    if period >= "2024-03":
        plants.append(unapproved_grant("GR-DE-001"))
    if period >= "2026-01":
        plants += [contribution_rate_break("NL-UK-001", "0.03"), vesting_typo("GR-US-002", "2027-03-01")]
    stale = (["GR-UK-003"] if period >= "2026-01" else []) + (["GR-US-003"] if period >= "2026-04" else [])
    if "2024-07" <= period <= "2026-02":
        stale.append("GR-US-241")
    if stale:
        plants.append(stale_vesting_flag(*stale))
    if period == "2026-03":
        plants += [paid_past_termination("NL-US-003", paid_through="2026-03-15"), JONAH_EMAIL, JONAH_REJECTION]
    if period == "2026-07":
        plants += [returning_leaver_paid("NL-US-242"), contribution_rate_break("NL-UK-186", "0.03")]
    if period == "2026-08":
        plants.append(duplicate_payment("PAY-2026-08-NL-DE-161"))
    if period in ("2026-09", "2026-10"):
        plants.append(department_miscoded("NL-US-245", "Engineering"))
    if period == "2026-09":
        plants.append(dropped_from_register("NL-US-246"))
    plants += held_out_plants(period)
    if period == COMPANY_OPEN:
        plants += [
            ghost_on_register(COMPANY_SAM),
            duplicate_grant("GR-US-006", "GR-US-006-DUP"),
            stale_home_entity("NL-DE-004"),
            duplicate_hr_row("NL-DE-003"),
            MAYA_EMAIL_OCTOBER,
            diverted_funding("NL-US-001", "9911882277"),
            uncleared_funding("PAY-2026-10-NL-US-002", "PAY-2026-10-NL-US-005"),
            late_bank_posting("PAY-2026-10-NL-DE-002", "2026-11-01"),
            uncleared_funding("PAY-2026-10-NL-DE-001"),
            funding_booked_in_full("DE"),
        ]
    return plants


def build_company_drop(period: str, extra: list[Plant] | None = None) -> dict:
    """One company month. Months after October are held out (D27); extra plants are an injected scenario."""
    if not COMPANY_FIRST <= period <= COMPANY_LAST:
        raise ValueError(f"The company world runs from {COMPANY_FIRST} to {COMPANY_LAST}; {period} is outside it.")
    return _build(month_of(period), list(_company_people()), company_plants(period) + list(extra or []), period < COMPANY_OPEN)


def scanned_email(title: str, lines: list[str], seed: int) -> Plant:
    """An email that reaches payroll as a scanned PDF: OCR-like misreads in the text, digits as printed."""
    from app.world.pdf import encode, render_pdf, scan_noise

    def apply(drop: Drop) -> None:
        drop.documents.append({"system": "email", "title": title, "body": encode(render_pdf(title, scan_noise(lines, seed)))})

    return Plant(f"scanned_email:{title}", "sources", apply)


def register_pdf(entity: str) -> Plant:
    """The provider also sends the entity's register as a PDF: the same rows, printed. Mess, not an error."""
    from app.world.pdf import encode, render_pdf

    def apply(drop: Drop) -> None:
        rows = [row for row in drop.payroll if row["entity_code"] == entity]
        title = f"{entity} payroll register {drop.month.period} (PDF)"
        lines = [title, f"Pay date: {drop.month.end}", "", "Employee    Department    Gross    Employer contribution"]
        lines += [f"{row['employee_number']}  {row['department']:<12}  {row['gross_wages']:>10}  {row['employer_contribution']:>10}" for row in rows]
        lines += ["", f"Lines: {len(rows)}"]
        drop.documents.append({"system": "payroll_pdf", "title": title, "body": encode(render_pdf(title, lines))})

    return Plant(f"register_pdf:{entity}", "sources", apply)


OSCAR_EMAIL_NOVEMBER = scanned_email(
    "Oscar Zimmer bank change email (scan)",
    [
        "From: oscar.zimmer@northlight.example",
        "To: payroll@northlight.example",
        "Date: 2026-11-03",
        "Subject: New bank details for November",
        "",
        "Hello payroll, please change my salary account before the November run.",
        "New account: 9922003344",
        "Sort code: 40-11-62",
        "Name: Oscar Zimmer",
        "Many thanks",
    ],
    seed=11,
)


def held_out_plants(period: str) -> list[Plant]:
    """What goes wrong in November and December. Vestly's code was not tuned on these (D27)."""
    if period == "2026-11":
        return [
            OSCAR_EMAIL_NOVEMBER,                                  # N1 bank change from a scanned email
            diverted_funding("NL-UK-102", "9922003344"),           # N1 and the provider paid the emailed account
            contribution_rate_break("NL-DE-103", "0.18"),          # N2 DE employer social at 18%, policy 20%
            duplicate_payment("PAY-2026-11-NL-US-103"),            # N3 November net pay sent twice
            paid_past_termination("NL-UK-103", "2026-11-25"),      # N4 HR not told yet: Nora paid her whole cycle
        ]
    if period == "2026-12":
        return [
            department_miscoded("NL-DE-104", "GTM"),               # D1 Product in HR, GTM on the register
            register_pdf("UK"),                                    # the UK register also arrives as a PDF
        ]
    return []


# ---------------------------------------------------------------- scenarios a judge can ask for

SCENARIO_KINDS = (
    "ghost_employee",
    "bank_change_by_email",
    "wrong_contribution",
    "duplicate_payment",
    "department_miscoded",
    "missing_from_register",
    # Not unapproved_grant: a drop can only change the platform's flag, while the signed grant and its board
    # approval stay on file, so the authority says approved and there is rightly nothing to catch.
    "vesting_typo",
    "duplicate_grant",
)


def scenario_plants(kind: str, employee_number: str, period: str, people: list[dict]) -> list[Plant]:
    """One planted error of a given kind, on a given person, in a given month, built from the same plant functions."""
    by_number = {person["employee_number"]: person for person in people}
    person = by_number.get(employee_number)
    if kind == "ghost_employee":
        if person is not None:
            raise ValueError(f"{employee_number} is a real employee; a ghost needs a number HR does not know.")
        ghost = {
            "employee_number": employee_number, "legal_name": "Alex Doe", "entity_code": "US", "department": "G&A",
            "hire_date": f"{period}-01", "monthly_rate": "7100.00", "bank_account": "9933004455",
        }
        return [ghost_on_register(ghost)]
    if person is None:
        raise ValueError(f"No employee {employee_number} in the company.")
    if kind == "bank_change_by_email":
        first, last = person["legal_name"].lower().split(" ", 1)
        account = "99" + employee_number[-3:].rjust(8, "7")
        lines = [
            f"From: {first}.{last.replace(' ', '')}@northlight.example", "To: payroll@northlight.example",
            f"Date: {period}-03", "Subject: bank change", "", "Please pay my salary to my new account from this month.",
            f"New account: {account}", f"Name: {person['legal_name']}",
        ]
        return [email(f"{person['legal_name']} bank change email (scenario)", lines), diverted_funding(employee_number, account)]
    if kind == "wrong_contribution":
        rate = {"US": "0.02", "UK": "0.03", "DE": "0.18"}[person["entity_code"]]
        return [contribution_rate_break(employee_number, rate)]
    if kind == "duplicate_payment":
        return [duplicate_payment(f"PAY-{period}-{employee_number}")]
    if kind == "department_miscoded":
        other = next(name for name in ("GTM", "Product", "Engineering", "G&A") if name != person["department"])
        return [department_miscoded(employee_number, other)]
    if kind == "missing_from_register":
        return [dropped_from_register(employee_number)]
    if kind in ("vesting_typo", "duplicate_grant"):
        grant = (person.get("grant") or {}).get("number")
        if grant is None:
            raise ValueError(f"{employee_number} has no grant.")
        if kind == "vesting_typo":
            return [vesting_typo(grant, "2031-01-01")]
        return [duplicate_grant(grant, f"{grant}-DUP")]
    raise ValueError(f"Unknown scenario kind {kind!r}; choose one of {', '.join(SCENARIO_KINDS)}.")


def company_manifest() -> dict:
    """One line per company month: the hash of its drop and its sizes. Committed, so generator drift shows."""
    import hashlib

    months = {}
    for period in company_periods():
        drop = build_company_drop(period)
        months[period] = {
            "sha256": hashlib.sha256(canonical(drop).encode("utf-8")).hexdigest(),
            "closed": drop["close_period"],
            "hr": len(drop["hr"]),
            "payroll": len(drop["payroll"]),
            "equity": len(drop["equity"]),
            "plants": drop["plants"],
        }
    return {"world": "company", "first": COMPANY_FIRST, "open": COMPANY_OPEN, "people": len(_company_people()), "months": months}


def company_manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "company" / "manifest.json"


def company_static_documents() -> list[dict]:
    """Signed grants and the approved compensation actions for the whole company, built in memory."""
    people = company_people()
    documents = [
        {"system": "signed_grant", "title": person["grant"]["number"], "body": signed_grant_text(person)}
        for person in people
        if "grant" in person
    ]
    documents.append({"system": "comp_action", "title": "Compensation actions 2018-2026", "body": json.dumps(comp_actions(people), indent=2) + "\n"})
    return documents


# ---------------------------------------------------------------- the held-out answer key (D27)
# Written from the generator's side: expected amounts come from the planted drops, never from a Vestly run.
# New entries are errors planted in the month; carried entries are October's problems still wrong in the
# month, which Vestly must keep handling the same way without booking anything twice.

HELDOUT_KEY_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "company" / "answer_key_heldout.json"
OCTOBER_KEY = HELDOUT_KEY_PATH.with_name("answer_key.json")
# October entries whose error is still in the month's sources, with the month's amount where it changes.
HELDOUT_CARRIED = {
    "2026-11": {"A02": None, "A03": None, "A06": "33000.00", "A07": None, "A09": None, "A13": None,
                "H01": None, "H02": None, "H03": None, "H04": None, "H05": None, "H07": None, "H08": None},
    # Not H02: the November close drafts Freya's catch-up, and a shortfall caught up by a live journal is not
    # reopened (D25). N03 is carried: Milo's November duplicate is still unrecovered, like the August one (H04).
    "2026-12": {"A02": None, "A03": None, "A06": "34000.00", "A07": None, "A09": None, "A13": None,
                "H01": None, "H03": None, "H04": None, "H05": None, "H07": None, "H08": None, "N03": None},
}


def _key_row(drop: dict, section: str, key: str, value: str) -> dict:
    [row] = [row for row in drop[section] if row[key] == value]
    return row


def _heldout_new_entries() -> list[dict]:
    november, december = build_company_drop("2026-11"), build_company_drop("2026-12")
    oscar = _key_row(november, "funding", "employee_number", "NL-UK-102")
    anton = _key_row(november, "payroll", "employee_number", "NL-DE-103")
    shortfall = (Decimal(anton["gross_wages"]) * Decimal("0.20") - Decimal(anton["employer_contribution"])).quantize(CENT)
    twice = _key_row(november, "funding", "payment_id", "PAY-2026-11-NL-US-103-2")
    nora = _key_row(november, "payroll", "employee_number", "NL-UK-103")
    days_after = inclusive_days("2026-11-14", nora["pay_period_end"])
    overpaid = (Decimal(nora["gross_wages"]) * days_after / inclusive_days(nora["pay_period_start"], nora["pay_period_end"])).quantize(CENT)
    wanda = _key_row(december, "payroll", "employee_number", "NL-DE-104")
    return [
        {
            "id": "N01", "name": "bank_change_from_scanned_email", "kind": "bank_detail_change", "period": "2026-11",
            "subjects": ["NL-UK-102"], "related": [oscar["payment_id"]], "trusted_source": "hr", "ask": "lee.park",
            "draft_allowed": False, "expected_outcome": "held", "reason_code": "bank_change_unverified",
            "must_cite": {"rules": ["BNK-1"], "authority_row": "pay_destination"},
            "must_not": [f"request_funding:{oscar['payment_id']}"],
            "expected_amount": None,
            "note": "Oscar Zimmer's bank change arrives as a scanned PDF with OCR misreads; the provider paid account 9922003344. "
                    "P-2026-03-001 is scoped to the US, so it must not be cited for a UK employee.",
            "scored_in": "2026-11",
        },
        {
            "id": "N02", "name": "de_social_rate_short", "kind": "contribution_rate_break", "period": "2026-11",
            "subjects": ["NL-DE-103"], "related": [], "trusted_source": "policy_rate", "ask": "payroll.provider",
            "draft_allowed": True, "expected_outcome": "ready", "reason_code": "contribution_error",
            "must_cite": {"rules": ["CTR-1"], "authority_row": "contribution_rate"}, "must_not": [],
            "expected_amount": str(shortfall), "expected_currency": "EUR",
            "note": f"Anton Yilmaz's employer social at 18% on {anton['gross_wages']} EUR; policy 20%: {shortfall} EUR short.",
            "scored_in": "2026-11",
        },
        {
            "id": "N03", "name": "november_pay_sent_twice", "kind": "duplicate_payment", "period": "2026-11",
            "subjects": [twice["payment_id"]], "related": ["PAY-2026-11-NL-US-103"], "trusted_source": "bank", "ask": "alex.moreau",
            "draft_allowed": False, "expected_outcome": "held", "reason_code": "duplicate_payment",
            "must_cite": {"rules": ["DUP-1"], "authority_row": "cash_moved"}, "must_not": [],
            "expected_amount": twice["amount"], "expected_currency": "USD",
            "note": "Milo Ueda's November net pay is in the payment file twice and the bank paid both.",
            "scored_in": "2026-11",
        },
        {
            "id": "N04", "name": "late_termination_found_a_month_later", "kind": "late_termination", "period": "2026-11",
            "subjects": ["NL-UK-103"], "related": [], "trusted_source": "hr", "ask": "lee.park",
            "draft_allowed": True, "expected_outcome": "ready", "reason_code": "paid_after_termination",
            "must_cite": {"authority_row": "employment_dates"}, "must_not": [],
            "expected_amount": str(overpaid), "expected_currency": "GBP",
            "note": f"Nora Walsh left on 13 November; HR keyed it on 3 December, so November paid her whole cycle. "
                    f"{days_after} of the cycle's days came after her exit: {overpaid} GBP to recover. HR did not know in November, "
                    "so the November close cannot catch it; December's must.",
            "scored_in": "2026-12",
        },
        {
            "id": "D01", "name": "department_miscoded_on_register", "kind": "department_mismatch", "period": "2026-12",
            "subjects": ["NL-DE-104"], "related": [], "trusted_source": "hr", "ask": "head.product",
            "draft_allowed": True, "expected_outcome": "ready", "reason_code": "department_mismatch",
            "must_cite": {"authority_row": "employment_dates"}, "must_not": [],
            "expected_amount": wanda["gross_wages"], "expected_currency": "EUR",
            "note": "Wanda Nakamura is Product in HR; the December register codes her pay to GTM. Wages follow HR.",
            "scored_in": "2026-12",
        },
    ]


def _heldout_carried_entries() -> list[dict]:
    october = {row["id"]: row for row in json.loads(OCTOBER_KEY.read_text(encoding="utf-8"))["anomalies"]}
    october.update({row["id"]: row for row in _heldout_new_entries()})
    out = []
    for period, ids in HELDOUT_CARRIED.items():
        for original, amount in ids.items():
            row = dict(october[original])
            row["id"] = f"{original}@{period}"
            row["name"] = f"carried:{row['name']}"
            row["period"] = period
            row["scored_in"] = period
            row["carried_from"] = original
            row["related"] = [item.replace("2026-10", period) for item in row.get("related", [])]
            row["must_not"] = [item.replace("PAY-2026-10", f"PAY-{period}") for item in row.get("must_not", [])]
            if amount is not None:
                row["expected_amount"] = amount
            if row.get("expected_amounts"):
                # A forfeited grant the platform still expenses: the reversal grows by its monthly expense each month.
                months = [item for item in HELD_OUT if item <= period]
                drops = [build_company_drop(month) for month in months]
                row["expected_amounts"] = [
                    {
                        **item,
                        "amount": money(
                            Decimal(item["amount"])
                            + sum((Decimal(_key_row(drop, "equity", "employee_number", item["subject"])["monthly_expense"]) for drop in drops), Decimal("0"))
                        ),
                    }
                    for item in row["expected_amounts"]
                ]
            row["note"] = f"Still wrong in {period}: {row['note']}"
            out.append(row)
    return out


def build_heldout_key() -> dict:
    return {
        "version": "heldout-2026-12",
        "world": "company",
        "note": "Held out (D27): November and December, planted after Vestly's detectors were written and scored without changing them. "
                "Carried entries are earlier problems still present in the month. Two corrections to this key, both before it was "
                "final and neither a code change: Nora Walsh's overpayment plant was missing (the provider had paid her correctly), "
                "and December first carried H02 (wrong under D25) and not N03 (still unrecovered).",
        "true_negatives": json.loads(OCTOBER_KEY.read_text(encoding="utf-8"))["true_negatives"],
        "unlabeled_mess": [
            {"id": "MD1", "name": "register_also_as_pdf", "period": "2026-12", "kind": None,
             "note": "The UK December register also arrives as a PDF. The same rows, printed: explain, do not flag."},
        ],
        "anomalies": _heldout_new_entries() + _heldout_carried_entries(),
    }


def write_heldout_key() -> Path:
    HELDOUT_KEY_PATH.write_text(json.dumps(build_heldout_key(), indent=2) + "\n", encoding="utf-8")
    return HELDOUT_KEY_PATH


if __name__ == "__main__":
    write_fixtures()
