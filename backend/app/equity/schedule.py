"""Grant-date fair value, straight-line over the service period, forfeitures when they occur.

The signed grant is the authority for terms. HR is the authority for who still works
where. The equity platform is checked, not trusted. Everything here works on facts
the caller read from the database; nothing comes from the world generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.ledger.chart import ENTITIES
from app.ledger.money import CENT, money_str
from app.periods import iter_periods, month_of, months_between

CLIFF_MONTHS = 12
CURRENCY = {code: currency for code, _name, currency in ENTITIES}
RATE_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class SignedGrant:
    grant_number: str
    employee_number: str
    legal_name: str
    home_entity: str
    grant_date: str
    cliff_end: str
    vest_end: str
    service_months: int
    shares: int
    fair_value: Decimal
    currency: str
    board_approved: bool
    board_approval_date: str | None
    content_hash: str | None


@dataclass(frozen=True)
class EmployeeFacts:
    """What HR has recorded about one person, as of the month being closed."""

    employee_number: str
    legal_name: str
    hire_date: str
    termination_date: str | None
    # (period, entity, department), one per month HR reported, oldest first
    assignments: tuple[tuple[str, str, str], ...]

    def assignment(self, period: str) -> tuple[str, str]:
        """HR's entity and department for a month. Before HR's first file, the first known assignment."""
        chosen = self.assignments[0]
        for row in self.assignments:
            if row[0] <= period:
                chosen = row
        return chosen[1], chosen[2]


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
    booking_currency: str
    fx_rate: Decimal | None
    booked_net_expense: Decimal | None


def parse_signed_grant(text: str, content_hash: str | None = None) -> SignedGrant:
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
        home_entity=lines["Home entity"].split()[0],
        grant_date=lines["Grant date"],
        cliff_end=lines["Cliff end"],
        vest_end=lines["Vest end"],
        service_months=int(lines["Service period"].split()[0]),
        shares=int(lines["Shares"]),
        fair_value=Decimal(value),
        currency=currency,
        board_approved=approved,
        board_approval_date=approval.split(",", 1)[1].strip() if approved and "," in approval else None,
        content_hash=content_hash,
    )


def translation_rate(from_currency: str, to_currency: str, rate_card: dict | None) -> Decimal | None:
    """Units of to_currency per unit of from_currency, through each currency's USD reference rate."""
    if from_currency == to_currency:
        return Decimal("1")
    if rate_card is None:
        return None

    def to_usd(currency: str) -> Decimal | None:
        if currency == "USD":
            return Decimal("1")
        value = rate_card.get(f"{currency.lower()}_usd_reference")
        return Decimal(value) if value is not None else None

    source, target = to_usd(from_currency), to_usd(to_currency)
    if source is None or target is None:
        return None
    return (source / target).quantize(RATE_PLACES)


def vested_shares(shares: int, service_month: int, service_months: int) -> int:
    if service_month < CLIFF_MONTHS:
        return 0
    if service_month >= service_months:
        return shares
    cliff = shares // 4
    rest = shares - cliff
    after_cliff = service_month - CLIFF_MONTHS
    return cliff + rest * after_cliff // (service_months - CLIFF_MONTHS)


def schedule_for(
    grant: SignedGrant,
    through_period: str,
    facts: EmployeeFacts,
    rate_cards: dict[str, dict] | None = None,
) -> list[GrantMonth]:
    rate_cards = rate_cards or {}
    monthly = (grant.fair_value / grant.service_months).quantize(CENT)
    cumulative = Decimal("0.00")
    status = "pending_approval"
    vested = 0
    rows: list[GrantMonth] = []
    for period in iter_periods(grant.grant_date[:7], through_period):
        month = month_of(period)
        service_month = months_between(grant.grant_date, month) + 1
        entity, department = facts.assignment(period)
        expense = Decimal("0.00")
        reversal = Decimal("0.00")
        serving = _serving(facts, period)
        ended_this_month = _forfeiture_month(facts, period)

        if not grant.board_approved:
            status = "pending_approval"
        elif status == "forfeited" or (not serving and not ended_this_month):
            status = "forfeited"
        elif period >= grant.vest_end[:7] and not ended_this_month:
            status = "vested"
            vested = grant.shares
            cumulative = grant.fair_value
        else:
            expense = monthly if service_month <= grant.service_months else Decimal("0.00")
            cumulative = (cumulative + expense).quantize(CENT)
            vested = vested_shares(grant.shares, min(service_month, grant.service_months), grant.service_months)
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
        net = (expense - reversal).quantize(CENT)
        booking_currency = CURRENCY[entity]
        rate = translation_rate(grant.currency, booking_currency, rate_cards.get(period))
        rows.append(
            GrantMonth(
                grant_number=grant.grant_number,
                employee_number=grant.employee_number,
                legal_name=facts.legal_name,
                period=period,
                entity_code=entity,
                department=department,
                currency=grant.currency,
                service_month=service_month,
                monthly_expense=expense,
                reversal=reversal,
                net_expense=net,
                cumulative_expense=cumulative,
                vested_shares=vested,
                vested_value=vested_value,
                status=status,
                board_approved=grant.board_approved,
                vest_end=grant.vest_end,
                cliff_end=grant.cliff_end,
                booking_currency=booking_currency,
                fx_rate=rate,
                booked_net_expense=None if rate is None else (net * rate).quantize(CENT),
            )
        )
    return rows


def _serving(facts: EmployeeFacts, period: str) -> bool:
    month = month_of(period)
    if facts.hire_date > month.end:
        return False
    if facts.termination_date is None:
        return True
    return facts.termination_date >= month.start


def _forfeiture_month(facts: EmployeeFacts, period: str) -> bool:
    return facts.termination_date is not None and facts.termination_date[:7] == period


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
        "booking_currency": row.booking_currency,
        "fx_rate": None if row.fx_rate is None else str(row.fx_rate),
        "booked_net_expense": None if row.booked_net_expense is None else money_str(row.booked_net_expense),
    }
