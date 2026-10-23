"""Grant-date fair value, straight-line over 48 months, forfeitures when they occur.

The signed grant is the authority for terms. HR is the authority for who still works
where. The equity platform is checked, not trusted.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.ledger.money import CENT, money_str
from app.world.drops import VEST_MONTHS, assignment, month_of, months_between, person_by_number

CLIFF_MONTHS = 12


@dataclass(frozen=True)
class SignedGrant:
    grant_number: str
    employee_number: str
    legal_name: str
    grant_date: str
    cliff_end: str
    vest_end: str
    shares: int
    fair_value: Decimal
    currency: str
    board_approved: bool
    board_approval_date: str | None
    body: str


@dataclass(frozen=True)
class GrantMonth:
    grant_number: str
    employee_number: str
    legal_name: str
    period: str
    entity_code: str
    department: str
    currency: str
    service_month: int
    monthly_expense: Decimal
    reversal: Decimal
    net_expense: Decimal
    cumulative_expense: Decimal
    vested_shares: int
    vested_value: Decimal
    status: str
    board_approved: bool
    vest_end: str
    cliff_end: str


def parse_signed_grant(text: str) -> SignedGrant:
    lines = {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip() for line in text.splitlines() if ":" in line}
    grant_number = text.splitlines()[0].rsplit(" ", 1)[-1]
    legal_name, number = lines["Employee"].rsplit(" (", 1)
    approval = lines["Board approved"]
    approved = approval.lower().startswith("yes")
    value, currency = lines["Grant-date fair value"].split()
    return SignedGrant(
        grant_number=grant_number,
        employee_number=number.rstrip(")"),
        legal_name=legal_name,
        grant_date=lines["Grant date"],
        cliff_end=lines["Cliff end"],
        vest_end=lines["Vest end"],
        shares=int(lines["Shares"]),
        fair_value=Decimal(value),
        currency=currency,
        board_approved=approved,
        board_approval_date=approval.split(",", 1)[1].strip() if approved and "," in approval else None,
        body=text,
    )


def load_signed_grants(directory: Path) -> list[SignedGrant]:
    return [parse_signed_grant(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.txt"))]


def iter_periods(start: str, end: str) -> list[str]:
    year, month = int(start[:4]), int(start[5:7])
    end_year, end_month = int(end[:4]), int(end[5:7])
    periods = []
    while (year, month) <= (end_year, end_month):
        periods.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return periods


def monthly_straight_line(fair_value: Decimal) -> Decimal:
    return (fair_value / VEST_MONTHS).quantize(CENT)


def vested_shares(shares: int, service_month: int) -> int:
    if service_month < CLIFF_MONTHS:
        return 0
    if service_month >= VEST_MONTHS:
        return shares
    cliff = shares // 4
    rest = shares - cliff
    after_cliff = service_month - CLIFF_MONTHS
    return cliff + rest * after_cliff // (VEST_MONTHS - CLIFF_MONTHS)


def schedule_for(grant: SignedGrant, through_period: str) -> list[GrantMonth]:
    person = person_by_number(grant.employee_number)
    monthly = monthly_straight_line(grant.fair_value)
    cumulative = Decimal("0.00")
    status = "pending_approval"
    vested = 0
    rows: list[GrantMonth] = []
    for period in iter_periods(grant.grant_date[:7], through_period):
        month = month_of(period)
        service_month = months_between(grant.grant_date, month) + 1
        entity, department, _, currency = assignment(person, month)
        expense = Decimal("0.00")
        reversal = Decimal("0.00")
        serving = _serving(person, period)
        ended_this_month = _forfeiture_month(person, period)

        if not grant.board_approved:
            status = "pending_approval"
        elif status == "forfeited" or (not serving and not ended_this_month):
            status = "forfeited"
        elif period >= grant.vest_end[:7] and not ended_this_month:
            status = "vested"
            vested = grant.shares
            cumulative = grant.fair_value
        else:
            expense = monthly if service_month <= VEST_MONTHS else Decimal("0.00")
            cumulative = (cumulative + expense).quantize(CENT)
            vested = vested_shares(grant.shares, min(service_month, VEST_MONTHS))
            vested_value = _vested_value(grant, vested)
            status = "cliff" if service_month < CLIFF_MONTHS else "vesting"
            if ended_this_month:
                if service_month < CLIFF_MONTHS:
                    reversal = cumulative
                    cumulative = Decimal("0.00")
                    vested = 0
                else:
                    reversal = max(Decimal("0.00"), (cumulative - vested_value).quantize(CENT))
                    cumulative = vested_value
                status = "forfeited"

        vested_value = _vested_value(grant, vested)
        if cumulative + CENT < vested_value:
            raise RuntimeError(f"{grant.grant_number} {period}: cumulative {cumulative} below vested {vested_value}")
        rows.append(
            GrantMonth(
                grant_number=grant.grant_number,
                employee_number=person["employee_number"],
                legal_name=person["legal_name"],
                period=period,
                entity_code=entity,
                department=department,
                currency=currency,
                service_month=service_month,
                monthly_expense=expense,
                reversal=reversal,
                net_expense=(expense - reversal).quantize(CENT),
                cumulative_expense=cumulative,
                vested_shares=vested,
                vested_value=vested_value,
                status=status,
                board_approved=grant.board_approved,
                vest_end=grant.vest_end,
                cliff_end=grant.cliff_end,
            )
        )
    return rows


def _serving(person: dict, period: str) -> bool:
    month = month_of(period)
    if person["hire_date"] > month.end:
        return False
    ended = person.get("termination_date")
    if ended is None:
        return True
    return ended >= month.start


def _forfeiture_month(person: dict, period: str) -> bool:
    ended = person.get("termination_date")
    return ended is not None and ended[:7] == period


def _vested_value(grant: SignedGrant, vested: int) -> Decimal:
    if vested == 0:
        return Decimal("0.00")
    return (grant.fair_value * vested / grant.shares).quantize(CENT)


def as_dict(row: GrantMonth) -> dict:
    return {
        "grant_number": row.grant_number,
        "employee_number": row.employee_number,
        "legal_name": row.legal_name,
        "period": row.period,
        "entity_code": row.entity_code,
        "department": row.department,
        "currency": row.currency,
        "service_month": row.service_month,
        "monthly_expense": money_str(row.monthly_expense),
        "reversal": money_str(row.reversal),
        "net_expense": money_str(row.net_expense),
        "cumulative_expense": money_str(row.cumulative_expense),
        "vested_shares": row.vested_shares,
        "vested_value": money_str(row.vested_value),
        "status": row.status,
        "board_approved": row.board_approved,
        "vest_end": row.vest_end,
        "cliff_end": row.cliff_end,
    }
