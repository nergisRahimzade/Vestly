"""Detectors: one deterministic check per kind of error that builds up between Northlight's systems.

Each returns findings. A finding names the fact, quotes what each source claims, ranks the
sources by the policy's authority table, names the trusted value, says who owns the question
(the routing map in the policy file), how big it is (materiality), whether Vestly may draft
anything for it yet, and the documents and rules it rests on.

Detectors are server code (decision D5). They read individual rows to find problems; the
model's tools see one person's rows only through an open case. Detection runs both ways:
from HR to payroll (a leaver still paid) and from payroll to HR (someone paid whom HR does
not know). Harmless mess gets an "explained" finding, never a conflict.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controls.callbacks import check_pay_destination
from app.equity.schedule import SignedGrant
from app.equity.service import department_subledger, grant_month, signed_grants, trueup_breakdown
from app.ledger.chart import ENTITIES
from app.ledger.models import Account, Entity, JournalEntry, JournalLine, Period
from app.ledger.money import CENT, money_str
from app.payroll.calc import overpaid_gross
from app.periods import Month, add_months, month_of
from app.policy.loader import Policy
from app.policy.materiality import MaterialityDecision, assess, rate_to
from app.record.evidence import document_basis, precedent_basis, rule_basis
from app.record.precedents import search_precedents
from app.world.models import (
    BankLine,
    EquitySnapshot,
    FundingInstruction,
    HrCensusRow,
    LedgerExportLine,
    PayrollRegisterRow,
    SourceDocument,
)

ZERO = Decimal("0.00")
CURRENCY = {code: currency for code, _name, currency in ENTITIES}
DEPARTMENT_HEADS = {
    "Engineering": "head.engineering",
    "Product": "head.product",
    "GTM": "head.gtm",
    "G&A": "head.ga",
}
# Detector kind -> the materiality kind that always goes to a person, where there is one.
ALWAYS_ESCALATE_AS = {
    "bank_detail_change": "bank_detail_change",
    "closed_period_effect": "closed_period_effect",
    "missing_grant_approval": "missing_grant_approval",
    "forfeiture_still_expensed": "forfeiture",
    "employment_dates_conflict": "forfeiture",
    "late_termination": "unconfirmed_person",
    "unconfirmed_person": "unconfirmed_person",
    "uncleared_funding": "funding_mismatch",
    "funding_booked_not_cleared": "funding_mismatch",
    "bank_account_reused": "bank_detail_change",
    "duplicate_payment": "funding_mismatch",
    "missing_from_register": "unconfirmed_person",
}
ACCOUNT_LINE = re.compile(r"(?im)^\s*(?:new\s+)?ac+o?u?n?t\s*:\s*(\d{6,})")
QUOTE_LINE = re.compile(r"(?im)^.*(?:a[c]+o?u?n?t|deposit|bank).*$")
FROM_LINE = re.compile(r"(?im)^from:\s*([^\s@]+)@")
NAME_LINE = re.compile(r"(?im)^name:\s*(.+)$")
DUPLICATE_SUFFIX = re.compile(r"[-_ ](dup|duplicate|copy)$", re.IGNORECASE)


@dataclass
class Finding:
    kind: str
    fact: str
    subject_number: str
    subject_name: str
    entity_code: str | None
    disposition: str
    authority: str
    trusted_value: str
    explanation: str
    ranking: list[dict]
    reason_code: str | None
    ask: list[str]
    amount: Decimal | None
    currency: str | None
    materiality: MaterialityDecision | None
    draft_allowed: bool | None
    basis: list[dict] = field(default_factory=list)
    details: dict = field(default_factory=dict)


@dataclass
class Context:
    """Everything the detectors read for one month, loaded once."""

    session: Session
    period: str
    policy: Policy
    month: Month
    rate_card: dict
    hr_rows: list[HrCensusRow]
    payroll: list[PayrollRegisterRow]
    funding: list[FundingInstruction]
    bank: list[BankLine]
    equity: list[EquitySnapshot]
    emails: list[SourceDocument]
    documents: dict[str, SourceDocument]
    signed: dict[str, SignedGrant]
    comp_actions: list[dict]
    comp_document: SourceDocument | None

    @property
    def hr(self) -> dict[str, HrCensusRow]:
        first: dict[str, HrCensusRow] = {}
        for row in self.hr_rows:
            first.setdefault(row.employee_number, row)
        return first


def load_context(session: Session, period: str, policy: Policy, rate_card: dict) -> Context:
    def rows(model):
        return list(session.scalars(select(model).where(model.period == period)))

    documents = {
        row.system: row
        for row in session.scalars(select(SourceDocument).where(SourceDocument.period == period))
        if row.system in ("hr", "payroll", "funding", "equity", "bank", "ledger")
    }
    comp_document = session.scalar(select(SourceDocument).where(SourceDocument.system == "comp_action"))
    return Context(
        session=session,
        period=period,
        policy=policy,
        month=month_of(period),
        rate_card=rate_card,
        hr_rows=rows(HrCensusRow),
        payroll=rows(PayrollRegisterRow),
        funding=rows(FundingInstruction),
        bank=rows(BankLine),
        equity=rows(EquitySnapshot),
        emails=list(
            session.scalars(
                select(SourceDocument).where(SourceDocument.period == period, SourceDocument.system == "email")
            )
        ),
        documents=documents,
        signed={grant.grant_number: grant for grant in signed_grants(session)},
        comp_actions=json.loads(comp_document.body) if comp_document is not None else [],
        comp_document=comp_document,
    )


def detect_all(ctx: Context) -> list[Finding]:
    from app.errors import doing

    findings: list[Finding] = []
    for detector in DETECTORS:
        with doing(f"running the {detector.__name__} detector", period=ctx.period):
            findings.extend(detector(ctx))
    return findings


# ---------------------------------------------------------------- helpers


def _owners(ctx: Context, kind: str, department: str | None = None) -> list[str]:
    owners = []
    for handle in ctx.policy.owners(kind):
        if handle == "department_head":
            if department in DEPARTMENT_HEADS:
                owners.append(DEPARTMENT_HEADS[department])
        else:
            owners.append(handle)
    return owners


def _ranking(ctx: Context, fact: str, claims: dict[str, str]) -> list[dict]:
    policy_fact = ctx.policy.fact(fact)
    return [
        {"rank": index, "source": item.source, "reason": item.reason, "claim": claims.get(item.source, "Not consulted.")}
        for index, item in enumerate(policy_fact.ranking, start=1)
    ]


def _materiality(ctx: Context, kind: str, amount: Decimal | None, currency: str | None, sources_agree: bool) -> MaterialityDecision:
    size = amount if amount is not None else ZERO
    unit = currency or "USD"
    return assess(
        ctx.policy,
        size,
        unit,
        rate=rate_to(ctx.rate_card, unit),
        kind=ALWAYS_ESCALATE_AS.get(kind),
        sources_agree=sources_agree,
    )


def _doc(ctx: Context, system: str, quote: str | None = None) -> list[dict]:
    document = ctx.documents.get(system)
    return [] if document is None else [document_basis(document.content_hash, document.title, quote)]


def _grant_doc(ctx: Context, grant_number: str) -> list[dict]:
    grant = ctx.signed.get(grant_number)
    if grant is None or grant.content_hash is None:
        return []
    return [document_basis(grant.content_hash, grant.grant_number)]


def _finding(ctx: Context, kind: str, fact: str, **values) -> Finding:
    values.setdefault("disposition", "conflict")
    if "authority" not in values:
        values["authority"] = ctx.policy.fact(fact).authority
    department = values.pop("department", None)
    if "ask" not in values:
        values["ask"] = _owners(ctx, kind, department)
    values.setdefault("basis", [])
    values.setdefault("details", {})
    if "materiality" not in values:
        values["materiality"] = _materiality(
            ctx, kind, values.get("amount"), values.get("currency"), values["disposition"] == "explained"
        )
    return Finding(kind=kind, fact=fact, **values)


def normalize_key(value: str) -> str:
    """A grant or payment key as a duplicate check compares it: case, separators and copy suffixes removed."""
    key = value.strip().upper()
    while True:
        stripped = DUPLICATE_SUFFIX.sub("", key)
        if stripped == key:
            break
        key = stripped
    key = re.sub(r"(?<![0-9])0+(?=[0-9])", "", key)
    return re.sub(r"[^A-Z0-9]", "", key)


def _locked(ctx: Context, entity_code: str, period: str) -> bool:
    row = ctx.session.scalar(
        select(Period)
        .join(Entity, Entity.id == Period.entity_id)
        .where(Entity.code == entity_code, Period.year == int(period[:4]), Period.month == int(period[5:7]))
    )
    return row is not None and row.status == "locked"


def pay_cycle_window(policy: Policy, entity_code: str, period: str) -> tuple[str, str]:
    """The full pay cycle a register row for this month covers."""
    month = month_of(period)
    if policy.pay_cycle(entity_code) == "26-25":
        previous = add_months(month.start, -1)
        return f"{previous[:7]}-26", f"{period}-25"
    return month.start, month.end


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


# ---------------------------------------------------------------- A01 bank-detail change by email


def _scan_basis(ctx: Context, document: SourceDocument) -> list[dict]:
    """A text read from a scanned PDF cites the scan too, so the decision traces to the document that arrived."""
    from app.world.pdf import source_pdf_hash

    scan_hash = source_pdf_hash(document.title)
    if scan_hash is None:
        return []
    scan = ctx.session.scalar(select(SourceDocument).where(SourceDocument.content_hash == scan_hash))
    return [] if scan is None else [document_basis(scan.content_hash, scan.title)]


def bank_detail_change(ctx: Context) -> list[Finding]:
    findings = []
    by_login = {row.legal_name.lower().replace(" ", "."): row for row in ctx.hr.values()}
    by_name = {row.legal_name.lower(): row for row in ctx.hr.values()}
    for email in ctx.emails:
        account_match = ACCOUNT_LINE.search(email.body)
        if account_match is None:
            continue
        account = account_match.group(1)
        sender = FROM_LINE.search(email.body)
        named = NAME_LINE.search(email.body)
        person = (by_login.get(sender.group(1).lower()) if sender else None) or (
            by_name.get(named.group(1).strip().lower()) if named else None
        )
        if person is None or account == person.bank_account:
            continue
        pay_date = ctx.month.end
        verdict = check_pay_destination(ctx.session, person.employee_number, account, ctx.period, pay_date)
        paid = [row for row in ctx.funding if row.employee_number == person.employee_number and row.destination_account == account]
        quote = account_match.group(0).strip()
        precedents = search_precedents(
            ctx.session, pay_date, rule="BNK-1", employee=person.employee_number, entity=person.entity_code
        )
        claims = {
            "hr": f"HR bank master for {person.legal_name} is account {person.bank_account}.",
            "email": f"An email asks to pay account {account} ('{quote}').",
        }
        provider = (
            f" The payroll provider already pointed {len(paid)} payment(s) at {account}"
            f" ({', '.join(row.payment_id for row in paid)}); "
            f"{'cleared' if any(row.cleared for row in paid) else 'not yet cleared'} at the bank."
            if paid
            else ""
        )
        cited = " ".join(f"{row.id} ({row.decided_by}, {row.decided_on}): {row.ruling}" for row in precedents)
        explanation = (
            f"Pay destination for {person.legal_name}. HR's bank master ({person.bank_account}) is the authority; "
            f"email is not a source for bank details.{provider} "
            + ("A confirmed call-back is on file, so the change is verified. " if verdict.allowed else f"{verdict.reason} ")
            + (f"Precedent: {cited}" if cited else "")
        ).strip()
        findings.append(
            _finding(
                ctx,
                "bank_detail_change",
                "pay_destination",
                subject_number=person.employee_number,
                subject_name=person.legal_name,
                entity_code=person.entity_code,
                disposition="explained" if verdict.allowed else "conflict",
                trusted_value=person.bank_account if not verdict.allowed else account,
                explanation=explanation,
                ranking=_ranking(ctx, "pay_destination", claims),
                reason_code=None if verdict.allowed else "bank_change_unverified",
                amount=sum((row.amount for row in paid), ZERO) or None,
                currency=CURRENCY[person.entity_code],
                draft_allowed=verdict.allowed,
                basis=[document_basis(email.content_hash, email.title, quote)]
                + _scan_basis(ctx, email)
                + [rule_basis("BNK-1")]
                + [precedent_basis(row.id, row.decided_by, row.decided_on) for row in precedents],
                details={
                    "requested_account": account,
                    "hr_account": person.bank_account,
                    "payments_to_requested_account": [row.payment_id for row in paid],
                    "precedents": [row.id for row in precedents],
                    "unverified": [] if verdict.allowed else ["call-back record"],
                },
            )
        )
    return findings


# ---------------------------------------------------------------- A02 late termination (HR -> payroll)


def employment_dates(ctx: Context) -> list[Finding]:
    """Four sources on a leaver's last day. late_termination when payroll paid past it."""
    findings = []
    for hr in ctx.hr.values():
        if hr.status != "terminated" or hr.termination_date is None:
            continue
        history = list(
            ctx.session.scalars(
                select(PayrollRegisterRow).where(PayrollRegisterRow.employee_number == hr.employee_number)
            )
        )
        equity = next((row for row in ctx.equity if row.employee_number == hr.employee_number), None)
        subledger = grant_month(ctx.session, ctx.period, hr.employee_number)
        ledger_line = _stock_expense_line(ctx, hr.entity_code, hr.department)
        department_net = department_subledger(ctx.session, ctx.period, hr.entity_code, hr.department)
        latest = max(history, key=lambda row: row.pay_period_end) if history else None
        paid_past = [row for row in history if row.pay_period_end > hr.termination_date]
        payroll_disagrees = bool(paid_past)
        equity_disagrees = equity is not None and (
            (equity.platform_status == "vesting" and equity.monthly_expense > 0)
            or (subledger is not None and equity.monthly_expense != subledger.booked_net_expense)
        )
        # The ledger's department total is evidence, not a trigger: at company scale one department holds many
        # people, so a department gap cannot be pinned on this leaver. Their own pay and grant can.
        if not (payroll_disagrees or equity_disagrees):
            continue
        claims = {
            "hr": f"status {hr.status}, termination_date {hr.termination_date}, recorded {hr.termination_recorded_on}.",
            "payroll": _payroll_claim(latest),
            "equity": _equity_claim(equity, subledger),
            "ledger": _ledger_claim(hr.entity_code, hr.department, ledger_line, department_net, subledger),
        }
        overpaid = sum(
            (overpaid_gross(row.gross_wages, row.pay_period_start, row.pay_period_end, hr.termination_date) for row in paid_past),
            ZERO,
        )
        kind = "late_termination" if payroll_disagrees else "employment_dates_conflict"
        explanation = (
            f"Employment end date for {hr.legal_name}. HR is the authority: {claims['hr']} "
            f"Payroll is downstream: {claims['payroll']} Equity platform: {claims['equity']} "
            f"Ledger: {claims['ledger']} Trusted value: {hr.termination_date}."
        )
        if payroll_disagrees:
            explanation += f" Pay for days after {hr.termination_date} is an overpayment of {money_str(overpaid)} to recover."
        findings.append(
            _finding(
                ctx,
                kind,
                "employment_dates",
                subject_number=hr.employee_number,
                subject_name=hr.legal_name,
                entity_code=hr.entity_code,
                trusted_value=hr.termination_date,
                explanation=explanation,
                ranking=_ranking(ctx, "employment_dates", claims),
                reason_code="paid_after_termination" if payroll_disagrees else "employment_dates_disagree",
                amount=overpaid if payroll_disagrees else None,
                currency=CURRENCY[hr.entity_code],
                draft_allowed=True,
                basis=_doc(ctx, "hr", claims["hr"]),
                department=hr.department,
                details={"paid_through": latest.pay_period_end if latest else None, "overpayment_from": [row.period for row in paid_past]},
            )
        )
    return findings


def _stock_expense_line(ctx: Context, entity_code: str, department: str) -> LedgerExportLine | None:
    return ctx.session.scalar(
        select(LedgerExportLine).where(
            LedgerExportLine.period == ctx.period,
            LedgerExportLine.entity_code == entity_code,
            LedgerExportLine.account_code == "5200",
            LedgerExportLine.department == department,
            LedgerExportLine.kind == "equity",
        )
    )


def _payroll_claim(latest: PayrollRegisterRow | None) -> str:
    if latest is None:
        return "No payroll register row in the loaded months."
    return f"Last pay period ends {latest.pay_period_end}, gross {money_str(latest.gross_wages)} {latest.currency}."


def _equity_claim(equity: EquitySnapshot | None, subledger) -> str:
    if equity is None:
        platform = "No equity snapshot for this person in the period."
    else:
        platform = (
            f"Grant {equity.grant_number} platform_status {equity.platform_status}, "
            f"monthly_expense {money_str(equity.monthly_expense)} {equity.currency}."
        )
    if subledger is None:
        return platform
    return (
        f"{platform} Subledger {subledger.grant_number} status {subledger.status}, "
        f"net_expense {money_str(subledger.booked_net_expense or subledger.net_expense)} {subledger.booking_currency}."
    )


def _ledger_claim(entity_code, department, line, department_net, subledger) -> str:
    if line is None or line.debit == 0:
        booked = f"No stock-compensation debit for {entity_code} {department} in this period."
    else:
        booked = f"Account 5200 debit {money_str(line.debit)} {line.currency} for {department} on '{line.entry_memo}'."
    grant = ""
    if subledger is not None:
        grant = (
            f" Grant {subledger.grant_number} subledger net "
            f"{money_str(subledger.booked_net_expense or subledger.net_expense)} {subledger.booking_currency}."
        )
    return f"{booked} Subledger {entity_code} {department} totals {money_str(department_net)}.{grant}"


# ---------------------------------------------------------------- A05 someone paid whom HR does not know (payroll -> HR)


def unconfirmed_person(ctx: Context) -> list[Finding]:
    known = set(ctx.hr)
    findings = []
    for row in ctx.payroll:
        if row.employee_number in known:
            continue
        cash = sum(
            (line.amount for line in ctx.funding if line.employee_number == row.employee_number and line.cleared), ZERO
        )
        claims = {
            "hr": f"No HR record for {row.employee_number} in {ctx.period}.",
            "payroll": f"Register pays {row.legal_name} gross {money_str(row.gross_wages)} {row.currency}.",
        }
        findings.append(
            _finding(
                ctx,
                "unconfirmed_person",
                "employment_dates",
                subject_number=row.employee_number,
                subject_name=row.legal_name,
                entity_code=row.entity_code,
                trusted_value="not employed",
                explanation=(
                    f"{row.legal_name} ({row.employee_number}) is on the {row.entity_code} register but not in HR. "
                    f"No wage expense until HR confirms the person. {money_str(cash)} {row.currency} already left "
                    f"the bank and belongs in payroll suspense (1210)."
                ),
                ranking=_ranking(ctx, "employment_dates", claims),
                reason_code="unknown_payee",
                amount=cash,
                currency=row.currency,
                draft_allowed=False,
                basis=_doc(ctx, "payroll", claims["payroll"]) + _doc(ctx, "hr"),
                details={"cash_already_paid": money_str(cash)},
            )
        )
    return findings


# ---------------------------------------------------------------- A04 backdated raise into a locked month; pay above what was authorized


def closed_period_effect(ctx: Context) -> list[Finding]:
    findings = []
    for row in ctx.payroll:
        if row.retro_effective_date is None or row.retro_gross is None:
            continue
        months = sorted(
            {
                period
                for period in _months_from(row.retro_effective_date[:7], ctx.period)
                if period < ctx.period and _locked(ctx, row.entity_code, period)
            }
        )
        if not months:
            continue
        action = next(
            (
                item
                for item in ctx.comp_actions
                if item["employee_number"] == row.employee_number and item["effective"] == row.retro_effective_date
            ),
            None,
        )
        entered_by = action["entered_by"] if action else None
        claims = {
            "approved_compensation": (
                f"Comp action effective {action['effective']}: {action['previous_monthly_rate']} to "
                f"{action['monthly_rate']} a month, keyed by {action['entered_by']} in {action['entered']}, "
                f"approved by {action['approved_by']}."
                if action
                else "No approved compensation action covers this retro pay."
            ),
            "payroll": (
                f"Register pays a retro lump of {money_str(row.retro_gross)} {row.currency} effective "
                f"{row.retro_effective_date} inside gross {money_str(row.gross_wages)}."
            ),
        }
        findings.append(
            _finding(
                ctx,
                "closed_period_effect",
                "authorized_compensation",
                subject_number=row.employee_number,
                subject_name=row.legal_name,
                entity_code=row.entity_code,
                trusted_value=action["monthly_rate"] if action else "not authorized",
                explanation=(
                    f"{row.legal_name}'s {ctx.month.label} pay includes {money_str(row.retro_gross)} {row.currency} for "
                    f"{', '.join(months)}, which are locked. The lump is held out of the {ctx.month.label} draft until the "
                    f"controller approves the catch-up; the month stays locked. "
                    + (f"{entered_by} keyed the change and cannot approve this run (SOD-2)." if entered_by else "")
                ),
                ranking=_ranking(ctx, "authorized_compensation", claims),
                reason_code="retro_pay_into_locked_period",
                amount=row.retro_gross,
                currency=row.currency,
                draft_allowed=False,
                basis=_doc(ctx, "payroll", claims["payroll"])
                + ([document_basis(ctx.comp_document.content_hash, ctx.comp_document.title)] if ctx.comp_document else [])
                + [rule_basis("CLS-1"), rule_basis("SOD-2")],
                details={"locked_months": months, "entered_by": entered_by, "held_gross": money_str(row.retro_gross)},
            )
        )
    return findings


def _months_from(start: str, end: str) -> list[str]:
    months, current = [], start
    while current <= end:
        months.append(current)
        current = add_months(f"{current}-01", 1)[:7]
    return months


def pay_above_authorized(ctx: Context) -> list[Finding]:
    """Gross pay against HR's rate for the days worked in the cycle, plus approved retro pay."""
    findings = []
    for row in ctx.payroll:
        hr = ctx.hr.get(row.employee_number)
        if hr is None or hr.status != "active" or hr.entity_code != row.entity_code:
            continue
        start, end = pay_cycle_window(ctx.policy, row.entity_code, ctx.period)
        monthly = hr.annual_salary / 12
        worked = _days(row.pay_period_start, row.pay_period_end)
        expected = (monthly * worked / _days(start, end)).quantize(CENT) if worked != _days(start, end) else monthly.quantize(CENT)
        expected += row.retro_gross or ZERO
        excess = (row.gross_wages - expected).quantize(CENT)
        if excess <= 0:
            continue
        claims = {
            "approved_compensation": f"HR rate {money_str(monthly)} a month; authorized gross {money_str(expected)}.",
            "payroll": f"Register gross {money_str(row.gross_wages)} for {row.pay_period_start} to {row.pay_period_end}.",
        }
        findings.append(
            _finding(
                ctx,
                "pay_above_authorized",
                "authorized_compensation",
                subject_number=row.employee_number,
                subject_name=row.legal_name,
                entity_code=row.entity_code,
                trusted_value=money_str(expected),
                explanation=f"{row.legal_name} was paid {money_str(excess)} {row.currency} more than HR's rate authorizes.",
                ranking=_ranking(ctx, "authorized_compensation", claims),
                reason_code="over_approved_pay",
                amount=excess,
                currency=row.currency,
                draft_allowed=False,
                basis=_doc(ctx, "payroll", claims["payroll"]) + _doc(ctx, "hr"),
            )
        )
    return findings


# ---------------------------------------------------------------- A07 employer contribution rate


def contribution_rate_break(ctx: Context) -> list[Finding]:
    findings = []
    for row in ctx.payroll:
        if row.employee_number not in ctx.hr:
            continue
        rate = ctx.policy.contribution_rate(row.entity_code)
        expected = (row.gross_wages * rate).quantize(CENT)
        difference = (expected - row.employer_contribution).quantize(CENT)
        if difference == 0:
            continue
        used = (row.employer_contribution / row.gross_wages * 100).quantize(Decimal("0.01")) if row.gross_wages else ZERO
        claims = {
            "policy_rate": f"Policy rate {rate * 100:.0f}% on gross {money_str(row.gross_wages)} = {money_str(expected)}.",
            "payroll": f"Register books {money_str(row.employer_contribution)} ({used}%).",
        }
        findings.append(
            _finding(
                ctx,
                "contribution_rate_break",
                "contribution_rate",
                subject_number=row.employee_number,
                subject_name=row.legal_name,
                entity_code=row.entity_code,
                trusted_value=money_str(expected),
                explanation=(
                    f"{row.legal_name}'s employer contribution is {money_str(row.employer_contribution)} {row.currency}; "
                    f"the policy rate gives {money_str(expected)}. Vestly drafts at the policy rate and asks the "
                    f"payroll provider to correct the register; the difference is {money_str(difference)}."
                ),
                ranking=_ranking(ctx, "contribution_rate", claims),
                reason_code="contribution_error",
                amount=abs(difference),
                currency=row.currency,
                draft_allowed=True,
                basis=_doc(ctx, "payroll", claims["payroll"]) + [rule_basis("CTR-1")],
                details={"policy_amount": money_str(expected), "register_amount": money_str(row.employer_contribution)},
            )
        )
    return findings


# ---------------------------------------------------------------- A11 transfer HR recorded but payroll and equity did not follow


def transfer_not_reflected(ctx: Context) -> list[Finding]:
    findings = []
    for hr in ctx.hr.values():
        pay = next((row for row in ctx.payroll if row.employee_number == hr.employee_number), None)
        grants = [row for row in ctx.equity if row.employee_number == hr.employee_number]
        stale = [
            source
            for source, entity in (("payroll", pay.entity_code if pay else None),)
            + tuple(("equity", row.entity_code) for row in grants)
            if entity is not None and entity != hr.entity_code
        ]
        if not stale:
            continue
        claims = {
            "hr": f"{hr.legal_name} is in {hr.entity_code} {hr.department} for {ctx.period}.",
            "payroll": f"Register runs {hr.legal_name} through {pay.entity_code} ({pay.currency})." if pay else "Not on the register.",
            "equity": "; ".join(f"Grant {row.grant_number} sits in {row.entity_code}." for row in grants) or "No grant.",
        }
        findings.append(
            _finding(
                ctx,
                "transfer_not_reflected",
                "employment_dates",
                subject_number=hr.employee_number,
                subject_name=hr.legal_name,
                entity_code=hr.entity_code,
                trusted_value=f"{hr.entity_code} {hr.department}",
                explanation=(
                    f"HR moved {hr.legal_name} to {hr.entity_code} {hr.department}, but {' and '.join(sorted(set(stale)))} "
                    f"still use the old entity. Wages and stock expense follow HR: the old entity's pay line is held "
                    f"until the provider re-runs it, and stock expense moves through the true-up."
                ),
                ranking=_ranking(ctx, "employment_dates", claims),
                reason_code="entity_mismatch",
                amount=pay.gross_wages if pay else None,
                currency=pay.currency if pay else None,
                draft_allowed=True,
                basis=_doc(ctx, "hr", claims["hr"]) + _doc(ctx, "payroll"),
                department=hr.department,
                details={"hr_entity": hr.entity_code, "stale_sources": sorted(set(stale)), "held_register_entity": pay.entity_code if pay else None},
            )
        )
    return findings


# ---------------------------------------------------------------- equity: A03 forfeitures, A06 approvals, A09 schedule, A10 duplicates


def equity_platform(ctx: Context) -> list[Finding]:
    findings = []
    breakdown = trueup_breakdown(ctx.session, ctx.period)
    by_grant: dict[str, list[dict]] = defaultdict(list)
    for line in breakdown:
        by_grant[line["grant_number"]].append(line)

    for grant_number, lines in sorted(by_grant.items()):
        reason = lines[0]["reason"]
        if reason not in ("forfeiture_still_expensed", "missing_grant_approval"):
            continue
        amount = sum((-Decimal(line["difference"]) for line in lines), ZERO)
        grant = ctx.signed[grant_number]
        hr = ctx.hr.get(grant.employee_number)
        claims = {
            "signed_grant": f"Signed grant {grant_number}: board approved {'yes' if grant.board_approved else 'no'}.",
            "hr": f"HR: {hr.status}, termination {hr.termination_date}." if hr else "No HR record.",
            "equity": "; ".join(
                f"Platform fed {line['platform_fed']} {line['currency']} to {line['entity_code']} {line['department']} "
                f"since {line.get('since', 'the last true-up')}; subledger earned {line['subledger']}."
                for line in lines
            ),
        }
        if reason == "missing_grant_approval":
            fact, kind = "grant_terms", "missing_grant_approval"
            trusted = "no board approval"
            text = (
                f"Grant {grant_number} for {grant.legal_name} has no board approval on the signed grant, yet the "
                f"platform expensed {money_str(amount)} {lines[0]['currency']}. No expense until approval (GRT-1); "
                f"the true-up reverses it, and legal is asked for the approval record."
            )
            basis_rules = [rule_basis("GRT-1")]
        else:
            fact, kind = "shares_outstanding", "forfeiture_still_expensed"
            trusted = f"forfeited {hr.termination_date}" if hr else "forfeited"
            text = (
                f"{grant.legal_name} left on {hr.termination_date if hr else 'an HR date'}; unvested shares are "
                f"forfeited, but the platform kept expensing grant {grant_number}. The true-up reverses "
                f"{money_str(amount)} {lines[0]['currency']}."
            )
            basis_rules = []
        findings.append(
            _finding(
                ctx,
                kind,
                fact,
                subject_number=grant.employee_number,
                subject_name=grant.legal_name,
                entity_code=lines[0]["entity_code"],
                trusted_value=trusted,
                explanation=text,
                ranking=_ranking(ctx, fact, claims),
                reason_code=kind,
                amount=amount,
                currency=lines[0]["currency"],
                draft_allowed=True if kind == "forfeiture_still_expensed" else False,
                basis=_grant_doc(ctx, grant_number) + _doc(ctx, "equity") + _doc(ctx, "hr") + basis_rules,
                details={"grant_number": grant_number, "trueup_lines": lines},
            )
        )

    signed_by_key = {normalize_key(number): grant for number, grant in ctx.signed.items()}
    for snapshot in ctx.equity:
        grant = ctx.signed.get(snapshot.grant_number)
        if grant is None:
            twin = signed_by_key.get(normalize_key(snapshot.grant_number))
            lines = by_grant.get(snapshot.grant_number, [])
            amount = sum((-Decimal(line["difference"]) for line in lines), ZERO)
            kind = "duplicate_grant" if twin is not None and twin.employee_number == snapshot.employee_number else "unknown_platform_grant"
            claims = {
                "signed_grant": f"Signed grant {twin.grant_number} exists for {twin.legal_name}." if twin else "No signed grant.",
                "equity": f"Platform also carries {snapshot.grant_number}, expensing {money_str(snapshot.monthly_expense)} a month.",
            }
            findings.append(
                _finding(
                    ctx,
                    kind,
                    "grant_terms",
                    subject_number=snapshot.employee_number,
                    subject_name=snapshot.legal_name,
                    entity_code=snapshot.entity_code,
                    trusted_value=twin.grant_number if twin else "no grant",
                    explanation=(
                        f"{snapshot.grant_number} on the platform is the same grant as signed {twin.grant_number} "
                        f"(same holder, same key once normalized). Expense it once; the true-up reverses "
                        f"{money_str(amount)} {snapshot.currency}."
                        if twin
                        else f"{snapshot.grant_number} on the platform has no signed grant behind it."
                    ),
                    ranking=_ranking(ctx, "grant_terms", claims),
                    reason_code=kind,
                    amount=amount or None,
                    currency=snapshot.currency,
                    draft_allowed=True,
                    basis=(_grant_doc(ctx, twin.grant_number) if twin else []) + _doc(ctx, "equity") + [rule_basis("DUP-1")],
                    details={"platform_grant": snapshot.grant_number, "signed_grant": twin.grant_number if twin else None},
                )
            )
            continue
        if snapshot.vest_end != grant.vest_end:
            claims = {
                "signed_grant": f"Signed grant {grant.grant_number} vests through {grant.vest_end}.",
                "equity": f"Platform shows vest end {snapshot.vest_end}.",
            }
            findings.append(
                _finding(
                    ctx,
                    "vest_schedule_mismatch",
                    "grant_terms",
                    subject_number=grant.employee_number,
                    subject_name=grant.legal_name,
                    entity_code=snapshot.entity_code,
                    trusted_value=grant.vest_end,
                    explanation=(
                        f"The platform's vest end for {grant.grant_number} ({snapshot.vest_end}) differs from the signed "
                        f"grant ({grant.vest_end}). Expense follows the signed schedule; legal is asked to fix the platform."
                    ),
                    ranking=_ranking(ctx, "grant_terms", claims),
                    reason_code="platform_schedule_typo",
                    amount=None,
                    currency=snapshot.currency,
                    draft_allowed=True,
                    basis=_grant_doc(ctx, grant.grant_number) + _doc(ctx, "equity"),
                    details={"signed_vest_end": grant.vest_end, "platform_vest_end": snapshot.vest_end},
                )
            )
    return findings


# ---------------------------------------------------------------- A08 and A12: funding against the bank


def funding_against_bank(ctx: Context) -> list[Finding]:
    findings = []
    cleared_ids = {row.payment_id for row in ctx.bank}
    for entity, _name, currency in ENTITIES:
        lines = [row for row in ctx.funding if row.entity_code == entity]
        if not lines:
            continue
        booked = _cash_booked(ctx, entity)
        banked = sum((row.amount for row in ctx.bank if row.entity_code == entity), ZERO)
        missing = [row for row in lines if row.payment_id not in cleared_ids]
        if booked > 0 and booked != banked:
            gap = (booked - banked).quantize(CENT)
            for row in missing:
                claims = {
                    "bank": f"No bank line for {row.payment_id}; {entity} bank file totals {money_str(banked)} {currency}.",
                    "payroll": f"Payment file instructs {money_str(row.amount)} {currency} to {row.legal_name}.",
                }
                findings.append(
                    _finding(
                        ctx,
                        "funding_booked_not_cleared",
                        "cash_moved",
                        subject_number=row.payment_id,
                        subject_name=row.legal_name,
                        entity_code=entity,
                        trusted_value="not cleared",
                        explanation=(
                            f"The {entity} ledger booked {money_str(booked)} {currency} of net pay as paid, but the bank "
                            f"shows {money_str(banked)}. The bank is the authority for cash moved: {row.payment_id} "
                            f"({money_str(row.amount)}) has not cleared. A reconciling item of {money_str(gap)} stays open "
                            f"until treasury confirms."
                        ),
                        ranking=_ranking(ctx, "cash_moved", claims),
                        reason_code="unreconciled_bank",
                        amount=row.amount,
                        currency=currency,
                        draft_allowed=True,
                        basis=_doc(ctx, "bank", claims["bank"]) + _doc(ctx, "ledger"),
                        details={"booked": money_str(booked), "banked": money_str(banked), "reconciling_item": money_str(gap)},
                    )
                )
            continue
        for row in missing:
            master = ctx.hr.get(row.employee_number)
            if master is None or master.bank_account != row.destination_account:
                # Not a legitimate line to re-send: the person or the account is in question elsewhere.
                continue
            claims = {
                "bank": f"No bank line for {row.payment_id}.",
                "payroll": f"Payment file instructs {money_str(row.amount)} {currency} to {row.legal_name} at {row.destination_account}.",
            }
            findings.append(
                _finding(
                    ctx,
                    "uncleared_funding",
                    "cash_moved",
                    subject_number=row.payment_id,
                    subject_name=row.legal_name,
                    entity_code=entity,
                    trusted_value="not cleared",
                    explanation=(
                        f"{row.payment_id} ({money_str(row.amount)} {currency} to {row.legal_name}) is in the payment "
                        f"file but the bank has not cleared it. Treasury is asked to re-send this line only, with its "
                        f"payment id as the idempotency key."
                    ),
                    ranking=_ranking(ctx, "cash_moved", claims),
                    reason_code="unreconciled_bank",
                    amount=row.amount,
                    currency=currency,
                    draft_allowed=True,
                    basis=_doc(ctx, "bank", claims["bank"]) + _doc(ctx, "payroll"),
                    details={"destination_account": row.destination_account},
                )
            )
    return findings


def _cash_booked(ctx: Context, entity: str) -> Decimal:
    """Cash credited to 1000 for net pay in this month's live entries for the entity."""
    year, month = int(ctx.period[:4]), int(ctx.period[5:7])
    rows = ctx.session.execute(
        select(JournalLine.credit)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .join(Period, Period.id == JournalEntry.period_id)
        .join(Entity, Entity.id == JournalEntry.entity_id)
        .join(Account, Account.id == JournalLine.account_id)
        .where(
            Entity.code == entity,
            Period.year == year,
            Period.month == month,
            Account.code == "1000",
            JournalEntry.status.in_(("approved", "posted")),
        )
    )
    return sum((row[0] for row in rows), ZERO)


# ---------------------------------------------------------------- explained mess


def explained_mess(ctx: Context) -> list[Finding]:
    findings = []
    seen: dict[str, HrCensusRow] = {}
    for row in ctx.hr_rows:
        first = seen.get(row.employee_number)
        if first is None:
            seen[row.employee_number] = row
            continue
        same = all(
            getattr(first, name) == getattr(row, name)
            for name in ("legal_name", "entity_code", "department", "status", "termination_date", "annual_salary", "bank_account")
        )
        findings.append(
            _finding(
                ctx,
                "duplicate_hr_row" if same else "conflicting_hr_rows",
                "employment_dates",
                subject_number=row.employee_number,
                subject_name=row.legal_name,
                entity_code=row.entity_code,
                disposition="explained" if same else "conflict",
                trusted_value="one person" if same else "unclear",
                explanation=(
                    f"{row.legal_name} appears twice in the {ctx.period} HR file with the same facts. Counted once; nothing to fix in the books."
                    if same
                    else f"{row.legal_name} appears twice in the HR file with different facts."
                ),
                ranking=_ranking(ctx, "employment_dates", {"hr": "Two identical rows." if same else "Two different rows."}),
                reason_code=None,
                amount=None,
                currency=None,
                draft_allowed=True,
                ask=[] if same else ["lee.park"],
                basis=_doc(ctx, "hr"),
            )
        )
    for row in ctx.bank:
        if row.posted_on <= ctx.month.end:
            continue
        findings.append(
            _finding(
                ctx,
                "bank_timing",
                "cash_moved",
                subject_number=row.payment_id,
                subject_name=row.description,
                entity_code=row.entity_code,
                disposition="explained",
                trusted_value=row.posted_on,
                explanation=(
                    f"{row.payment_id} cleared on {row.posted_on}, after month-end. A timing lag, not an unpaid wage; "
                    f"it is in this month's bank file."
                ),
                ranking=_ranking(ctx, "cash_moved", {"bank": f"Posted {row.posted_on}, {money_str(row.amount)} {row.currency}."}),
                reason_code=None,
                amount=None,
                currency=row.currency,
                draft_allowed=True,
                ask=[],
                basis=_doc(ctx, "bank"),
            )
        )
    return findings


def fx_band(ctx: Context) -> list[Finding]:
    card = ctx.rate_card
    if "gbp_usd_reference" not in card or "gbp_usd_reporting" not in card:
        # The month-end rate is not published yet (or the pack gave no rate): the check waits, it does not guess.
        return [
            _finding(
                ctx,
                "fx_translation",
                "gbp_usd_translation",
                subject_number="GBPUSD",
                subject_name="GBPUSD",
                entity_code=None,
                disposition="pending",
                trusted_value="not yet published",
                explanation=(
                    f"GBPUSD translation for {ctx.period} waits for the end-of-day rate of {ctx.month.end}; "
                    "nothing is translated at another day's rate."
                ),
                ranking=_ranking(ctx, "gbp_usd_translation", {}),
                reason_code="fx_rate_pending",
                amount=None,
                currency=None,
                draft_allowed=False,
                ask=[],
            )
        ]
    reference = Decimal(card["gbp_usd_reference"])
    used = Decimal(card["gbp_usd_reporting"])
    gap = abs(reference - used) / reference
    inside = gap <= ctx.policy.fx_band
    claims = {
        "reference_rate": f"{card['gbp_usd_reference']}, Open Exchange Rates end of day {card.get('rate_date')}.",
        "management_pack": f"{card['gbp_usd_reporting']} used in the management pack.",
        "ledger": "UK books stay in GBP. This gap does not rewrite a journal.",
    }
    percent = lambda rate: f"{(rate * 100).quantize(Decimal('0.01')):.2f}".rstrip("0").rstrip(".")  # noqa: E731
    return [
        _finding(
            ctx,
            "fx_translation",
            "gbp_usd_translation",
            subject_number="GBPUSD",
            subject_name="GBPUSD",
            entity_code=None,
            disposition="explained" if inside else "conflict",
            trusted_value=card["gbp_usd_reference"],
            explanation=(
                f"GBPUSD translation for {ctx.period}. The reference rate is {card['gbp_usd_reference']} and the "
                f"management pack used {card['gbp_usd_reporting']}. The difference is {percent(gap)}% of the reference "
                f"rate, {'inside' if inside else 'outside'} the policy band of {percent(ctx.policy.fx_band)}%. "
                + ("This is an explained difference, not an exception." if inside else "This is outside the band and needs a person.")
            ),
            ranking=_ranking(ctx, "gbp_usd_translation", claims),
            reason_code=None if inside else "fx_outside_band",
            amount=None,
            currency=None,
            draft_allowed=True,
            ask=[] if inside else ["dana.ruiz"],
        )
    ]


# ---------------------------------------------------------------- history: errors only the loaded months show (task 17)
#
# These read every loaded month (January 2022 on in the company world), not just the one being closed.
# Each stays silent on clean history and names the months it rests on.


def _history(ctx: Context, model, *conditions):
    return list(ctx.session.scalars(select(model).where(model.period <= ctx.period, *conditions).order_by(model.period)))


def _period_documents(ctx: Context, system: str, periods) -> list[dict]:
    rows = ctx.session.scalars(
        select(SourceDocument).where(SourceDocument.system == system, SourceDocument.period.in_(sorted(set(periods))))
    )
    return [document_basis(row.content_hash, row.title) for row in rows]


def _live_key(ctx: Context, booking_key: str) -> bool:
    from app.ledger.service import live_entry_for_key

    return live_entry_for_key(ctx.session, booking_key) is not None


def contribution_shortfalls(
    session: Session, period: str, policy: Policy, include_booked: bool = False
) -> dict[str, list[PayrollRegisterRow]]:
    """Closed-month register lines whose employer contribution is not the policy rate, for people whose current
    month is right (a current-month break is its own case and covers the person's whole problem). Lines already
    caught up by a live journal are left out, unless include_booked (the payroll service reports them as on file)."""
    from app.ledger.service import LIVE_STATUSES

    current = {
        row.employee_number
        for row in session.scalars(select(PayrollRegisterRow).where(PayrollRegisterRow.period == period))
        if (row.gross_wages * policy.contribution_rate(row.entity_code)).quantize(CENT) != row.employer_contribution
    }
    known = set(session.scalars(select(HrCensusRow.employee_number).where(HrCensusRow.period <= period)))
    from app.auth.identities import SEATS

    # Every live wage and catch-up key at once (one query, not one per register line).
    live = dict(
        session.execute(
            select(JournalEntry.booking_key, JournalEntry.prepared_by).where(
                JournalEntry.status.in_(LIVE_STATUSES),
                JournalEntry.booking_key.like("payroll:wages:%") | JournalEntry.booking_key.like("payroll:contribution_catchup:%"),
            )
        ).all()
    )
    found: dict[str, list[PayrollRegisterRow]] = defaultdict(list)
    for row in session.scalars(select(PayrollRegisterRow).where(PayrollRegisterRow.period < period).order_by(PayrollRegisterRow.period)):
        if row.employee_number in current or row.employee_number not in known:
            continue
        # A month Vestly drafted itself was booked at the policy rate already; only the old process's months can be short.
        if live.get(f"payroll:wages:{row.entity_code}:{row.period}") in SEATS:
            continue
        expected = (row.gross_wages * policy.contribution_rate(row.entity_code)).quantize(CENT)
        if expected == row.employer_contribution:
            continue
        if not include_booked and f"payroll:contribution_catchup:{row.employee_number}:{row.period}" in live:
            continue
        found[row.employee_number].append(row)
    return found


def contribution_shortfall(ctx: Context) -> list[Finding]:
    findings = []
    for number, rows in sorted(contribution_shortfalls(ctx.session, ctx.period, ctx.policy).items()):
        latest = rows[-1]
        rate = ctx.policy.contribution_rate(latest.entity_code)
        months = [row.period for row in rows]
        shortfall = sum(((row.gross_wages * rate).quantize(CENT) - row.employer_contribution for row in rows), ZERO)
        claims = {
            "policy_rate": f"Policy rate {rate * 100:.0f}% of gross in {', '.join(months)}.",
            "payroll": "; ".join(
                f"{row.period}: booked {money_str(row.employer_contribution)} on gross {money_str(row.gross_wages)}" for row in rows
            ),
        }
        findings.append(
            _finding(
                ctx,
                "contribution_shortfall",
                "contribution_rate",
                subject_number=number,
                subject_name=latest.legal_name,
                entity_code=latest.entity_code,
                trusted_value=f"{rate}",
                explanation=(
                    f"{latest.legal_name}'s employer contribution in {', '.join(months)} (closed) was below the policy rate "
                    f"by {money_str(shortfall)} {latest.currency}. The months stay closed: the shortfall is caught up once, "
                    f"in {ctx.month.label}, and the payroll provider is asked to explain the rate."
                ),
                ranking=_ranking(ctx, "contribution_rate", claims),
                reason_code="contribution_error_closed_month",
                amount=shortfall,
                currency=latest.currency,
                draft_allowed=True,
                basis=_period_documents(ctx, "payroll", months) + [rule_basis("CTR-1")],
                details={"months": months, "per_month": {row.period: money_str((row.gross_wages * rate).quantize(CENT) - row.employer_contribution) for row in rows}},
            )
        )
    return findings


def bank_account_reused(ctx: Context) -> list[Finding]:
    """An active person's HR bank account that someone else was set up with or paid to in any loaded month."""
    holders: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in _history(ctx, HrCensusRow):
        holders[row.bank_account][row.employee_number].append(row.period)
    for row in _history(ctx, FundingInstruction):
        holders[row.destination_account][row.employee_number].append(row.period)
    findings = []
    for hr in ctx.hr.values():
        if hr.status != "active":
            continue
        others = {number: periods for number, periods in holders.get(hr.bank_account, {}).items() if number != hr.employee_number}
        if not others:
            continue
        names = {
            number: ctx.session.scalar(select(HrCensusRow.legal_name).where(HrCensusRow.employee_number == number).limit(1)) or number
            for number in others
        }
        earlier = "; ".join(f"{names[number]} ({number}) {min(periods)} to {max(periods)}" for number, periods in sorted(others.items()))
        precedents = search_precedents(ctx.session, ctx.month.end, rule="BNK-1", employee=hr.employee_number, entity=hr.entity_code)
        claims = {
            "hr": f"HR bank master for {hr.legal_name} is {hr.bank_account}.",
            "email": f"The same account belonged to {earlier}.",
        }
        findings.append(
            _finding(
                ctx,
                "bank_account_reused",
                "pay_destination",
                subject_number=hr.employee_number,
                subject_name=hr.legal_name,
                entity_code=hr.entity_code,
                trusted_value="unverified",
                explanation=(
                    f"{hr.legal_name}'s pay goes to account {hr.bank_account}, which the history shows was {earlier}. "
                    f"One account for two people is held until HR confirms the account with {hr.legal_name} by call-back (BNK-1)."
                ),
                ranking=_ranking(ctx, "pay_destination", claims),
                reason_code="bank_account_reused",
                amount=None,
                currency=CURRENCY[hr.entity_code],
                draft_allowed=False,
                basis=_doc(ctx, "hr", claims["hr"]) + [rule_basis("BNK-1")] + [precedent_basis(row.id, row.decided_by, row.decided_on) for row in precedents],
                details={"account": hr.bank_account, "earlier_holders": {number: [min(periods), max(periods)] for number, periods in others.items()}},
            )
        )
    return findings


def duplicate_payment(ctx: Context) -> list[Finding]:
    """The same pay paid twice: two cleared lines for one person, month, kind and amount."""
    cleared = {row.payment_id for row in _history(ctx, BankLine)}
    groups: dict[tuple, list[FundingInstruction]] = defaultdict(list)
    for row in _history(ctx, FundingInstruction):
        if row.payment_id in cleared:
            groups[(row.employee_number, row.period, row.kind, row.amount)].append(row)
    findings = []
    for (_number, period, _kind, amount), rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        first, *extra = sorted(rows, key=lambda row: row.payment_id)
        for row in extra:
            claims = {
                "bank": f"The bank cleared both {first.payment_id} and {row.payment_id}, {money_str(amount)} {row.currency} each.",
                "payroll": f"One {period} net pay is owed to {row.legal_name}.",
            }
            findings.append(
                _finding(
                    ctx,
                    "duplicate_payment",
                    "cash_moved",
                    subject_number=row.payment_id,
                    subject_name=row.legal_name,
                    entity_code=row.entity_code,
                    trusted_value=f"paid twice; {first.payment_id} is the one owed",
                    explanation=(
                        f"{row.legal_name}'s {period} net pay went out twice ({first.payment_id} and {row.payment_id}). "
                        f"{money_str(amount)} {row.currency} is to be recovered; treasury is asked, and nothing is paid again."
                    ),
                    ranking=_ranking(ctx, "cash_moved", claims),
                    reason_code="duplicate_payment",
                    amount=amount,
                    currency=row.currency,
                    draft_allowed=False,
                    basis=_period_documents(ctx, "bank", [period]) + [rule_basis("DUP-1")],
                    details={"paid_twice": [first.payment_id, row.payment_id], "period": period},
                )
            )
    return findings


def salary_without_approval(ctx: Context) -> list[Finding]:
    """A rise in HR's salary that no approved compensation action covers, found by walking the salary history."""
    from app.memory.history import comp_actions

    actions = defaultdict(list)
    for item in comp_actions(ctx.session):
        actions[item["employee_number"]].append(item)
    by_person: dict[str, dict[str, HrCensusRow]] = defaultdict(dict)
    for row in _history(ctx, HrCensusRow):
        by_person[row.employee_number][row.period] = row
    findings = []
    for hr in ctx.hr.values():
        if hr.status != "active":
            continue
        history = [by_person[hr.employee_number][period] for period in sorted(by_person[hr.employee_number])]
        approved = (history[0].annual_salary / 12).quantize(CENT)
        unapproved_from = None
        for row in history[1:]:
            salary = (row.annual_salary / 12).quantize(CENT)
            if salary <= approved:
                continue
            covered = any(
                Decimal(item["monthly_rate"]) == salary and item["effective"] <= month_of(row.period).end and item["entered"] <= row.period
                for item in actions[hr.employee_number]
            )
            if covered:
                approved = salary
            elif unapproved_from is None:
                unapproved_from = row.period
        current = (hr.annual_salary / 12).quantize(CENT)
        if unapproved_from is None or current <= approved:
            continue
        excess = current - approved
        claims = {
            "approved_compensation": f"The last approved pay for {hr.legal_name} is {money_str(approved)} a month.",
            "payroll": f"HR and the register have paid {money_str(current)} a month since {unapproved_from}.",
        }
        findings.append(
            _finding(
                ctx,
                "salary_without_approval",
                "authorized_compensation",
                subject_number=hr.employee_number,
                subject_name=hr.legal_name,
                entity_code=hr.entity_code,
                trusted_value=money_str(approved),
                explanation=(
                    f"{hr.legal_name}'s salary rose to {money_str(current)} {hr.currency} a month from {unapproved_from}, "
                    f"but no approved compensation action covers it; the last approved rate is {money_str(approved)}. "
                    f"{money_str(excess)} a month is above what was approved."
                ),
                ranking=_ranking(ctx, "authorized_compensation", claims),
                reason_code="salary_without_approval",
                amount=excess,
                currency=hr.currency,
                draft_allowed=False,
                basis=([document_basis(ctx.comp_document.content_hash, ctx.comp_document.title)] if ctx.comp_document else []) + _doc(ctx, "hr"),
                details={"approved_monthly": money_str(approved), "paid_monthly": money_str(current), "unapproved_from": unapproved_from},
            )
        )
    return findings


def department_mismatch(ctx: Context) -> list[Finding]:
    """The register codes pay to a department HR does not show, in the same entity. Wages follow HR."""
    findings = []
    for row in ctx.payroll:
        hr = ctx.hr.get(row.employee_number)
        if hr is None or hr.entity_code != row.entity_code or hr.department == row.department:
            continue
        since = [
            item.period
            for item in _history(ctx, PayrollRegisterRow, PayrollRegisterRow.employee_number == row.employee_number)
            if item.department == row.department and item.department != hr.department
        ]
        claims = {
            "hr": f"HR has {hr.legal_name} in {hr.entity_code} {hr.department}.",
            "payroll": f"The register codes the pay to {row.department} ({', '.join(since)}).",
        }
        findings.append(
            _finding(
                ctx,
                "department_mismatch",
                "employment_dates",
                subject_number=row.employee_number,
                subject_name=row.legal_name,
                entity_code=row.entity_code,
                trusted_value=hr.department,
                explanation=(
                    f"{row.legal_name} is in {hr.department} in HR, but the register has coded the pay to {row.department} "
                    f"since {since[0]}. Vestly books the wages to {hr.department}, as HR says; the department head is asked."
                ),
                ranking=_ranking(ctx, "employment_dates", claims),
                reason_code="department_mismatch",
                amount=row.gross_wages,
                currency=row.currency,
                draft_allowed=True,
                department=hr.department,
                basis=_doc(ctx, "hr", claims["hr"]) + _period_documents(ctx, "payroll", since),
                details={"hr_department": hr.department, "register_department": row.department, "months": since},
            )
        )
    return findings


def missing_from_register(ctx: Context) -> list[Finding]:
    """Someone HR has as active and hired in the cycle, missing from that month's register: not paid."""
    paid = {(row.period, row.employee_number) for row in _history(ctx, PayrollRegisterRow)}
    missing: dict[str, list[tuple[str, Decimal, HrCensusRow]]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for row in _history(ctx, HrCensusRow):
        if row.status != "active" or (row.period, row.employee_number) in seen:
            continue
        seen.add((row.period, row.employee_number))
        start, end = pay_cycle_window(ctx.policy, row.entity_code, row.period)
        if row.hire_date > end or (row.period, row.employee_number) in paid:
            continue
        first = max(start, row.hire_date)
        monthly = row.annual_salary / 12
        owed = (monthly * _days(first, end) / _days(start, end)).quantize(CENT) if first != start else monthly.quantize(CENT)
        missing[row.employee_number].append((row.period, owed, row))
    findings = []
    for number, months in sorted(missing.items()):
        latest = months[-1][2]
        owed = sum((amount for _period, amount, _row in months), ZERO)
        periods = [period for period, _amount, _row in months]
        claims = {
            "hr": f"HR has {latest.legal_name} active from {latest.hire_date}.",
            "payroll": f"No register line for {latest.legal_name} in {', '.join(periods)}.",
        }
        findings.append(
            _finding(
                ctx,
                "missing_from_register",
                "employment_dates",
                subject_number=number,
                subject_name=latest.legal_name,
                entity_code=latest.entity_code,
                trusted_value="employed and not paid",
                explanation=(
                    f"{latest.legal_name} is employed (HR, from {latest.hire_date}) but was not on the register in "
                    f"{', '.join(periods)}: {money_str(owed)} {latest.currency} of pay is owed. The provider is asked to pay it."
                ),
                ranking=_ranking(ctx, "employment_dates", claims),
                reason_code="not_on_register",
                amount=owed,
                currency=latest.currency,
                draft_allowed=False,
                basis=_period_documents(ctx, "hr", periods) + _period_documents(ctx, "payroll", periods),
                details={"months": periods, "owed": money_str(owed)},
            )
        )
    return findings


DETECTORS = (
    bank_detail_change,
    employment_dates,
    unconfirmed_person,
    closed_period_effect,
    pay_above_authorized,
    contribution_rate_break,
    transfer_not_reflected,
    equity_platform,
    funding_against_bank,
    explained_mess,
    fx_band,
    contribution_shortfall,
    bank_account_reused,
    duplicate_payment,
    salary_without_approval,
    department_mismatch,
    missing_from_register,
)


def payroll_holds(session: Session, period: str, policy: Policy, rate_card: dict) -> dict[str, dict]:
    """What the payroll draft must leave out until a person answers: employee -> held gross and why."""
    ctx = load_context(session, period, policy, rate_card)
    holds: dict[str, dict] = {}
    for finding in closed_period_effect(ctx):
        holds[finding.subject_number] = {"gross": finding.amount, "kind": finding.kind, "reason": finding.explanation}
    for finding in transfer_not_reflected(ctx):
        if "payroll" in finding.details["stale_sources"]:
            holds[finding.subject_number] = {"gross": None, "kind": finding.kind, "reason": finding.explanation}
    return holds
