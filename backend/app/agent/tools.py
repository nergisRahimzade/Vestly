"""The agent's tools. Read, compute, and gated actions; approve, post, release, reopen and policy edits are absent.

Each tool says in its description what it is for and what it is not for. Each returns a
ToolResult, and code (never the model) decides which outcomes that result establishes.
Reads of one person's rows stay inside the case (PRV-1). Amounts are never taken from the
model: drafts and funding come from the deterministic services and the payment file.
"""

import json
import re
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from sqlalchemy import select

from app.agent.registry import ToolInput, register
from app.agent.results import ToolResult
from app.auth.identities import CONTROLLER_HANDLE
from app.cases import service as cases
from app.controls.clock import today
from app.controls.gate import deny, enforce
from app.controls.models import BankTransfer, FundingLine, FundingRequest
from app.equity.service import signed_grants, trueup_breakdown
from app.ledger.models import JournalEntry
from app.ledger.money import CENT, money_str
from app.ledger.service import find_period, list_entries, require_entity
from app.periods import month_of
from app.policy.loader import load_policy
from app.policy.materiality import rate_to
from app.reconcile.detectors import normalize_key
from app.reconcile.models import Reconciliation
from app.record.evidence import record
from app.record.precedents import as_dict as precedent_dict
from app.record.precedents import search_precedents
from app.world.loader import rate_card_for
from app.world.models import (
    BankLine,
    DropLoad,
    EquitySnapshot,
    FundingInstruction,
    HrCensusRow,
    PayrollRegisterRow,
    SourceDocument,
)

ZERO = Decimal("0.00")
JOURNAL_OUTCOMES = {"draft": ("drafted", "pending_approval"), "approved": ("approved",), "posted": ("posted",), "rejected": ("rejected",)}
FUNDING_OUTCOMES = {"requested": ("requested", "pending_approval"), "sent": ("requested",), "confirmed": ("paid",)}

# run_tests: the workspace and the command are fixed in code; the model passes nothing.
TEST_WORKSPACE = Path(__file__).resolve().parents[2]
TEST_TIMEOUT_SECONDS = 900
SUMMARY_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected)")

# convert_currency: periods whose reference-rate feed is down. Simulated, like the bank.
FX_FEED_DOWN: set[str] = set()


class NoInput(ToolInput):
    pass


class PersonInput(ToolInput):
    employee_number: str


class GrantInput(ToolInput):
    grant_number: str


# ---------------------------------------------------------------- helpers


def _people(ctx) -> set[str]:
    """The people this case is about: its subject, or the person a payment line pays."""
    subject = ctx.case.subject_number
    line = ctx.session.scalar(select(FundingInstruction).where(FundingInstruction.payment_id == subject))
    return {line.employee_number} if line is not None else {subject}


def _require_person(ctx, employee_number: str) -> None:
    if employee_number not in _people(ctx):
        enforce(
            deny("PRV-1", f"{employee_number} is not the subject of case {ctx.case.key}."), ctx.seat, "read_scoped"
        )


def _journal_establishes(entries) -> list[tuple[str, str]]:
    return [(entry.number, outcome) for entry in entries for outcome in JOURNAL_OUTCOMES.get(entry.status, ())]


# ---------------------------------------------------------------- reads


@register(
    "get_case", "read", ("payroll", "equity"), "read_scoped",
    """The case you are working: its finding (what each source claims, which one is trusted and why), its steps,
    the questions asked and their answers, and the records linked to it (journals, funding requests) with their
    status. Start here. Not for other people's cases.""",
    NoInput,
)
def get_case(ctx, args: NoInput) -> ToolResult:
    timeline = cases.timeline(ctx.session, ctx.case)
    establishes = []
    if ctx.case.outcome:
        establishes.append((ctx.case.subject_number, ctx.case.outcome))
    if ctx.case.state == "closed":
        establishes.append((ctx.case.subject_number, "closed"))
    establishes += [(question["asked_to"], "asked") for question in timeline["questions"]]
    for link in timeline["links"]:
        if link["resource"] == "journal_entry":
            entry = ctx.session.get(JournalEntry, link["id"])
            if entry is not None:
                establishes += _journal_establishes([entry])
                link["number"], link["status"] = entry.number, entry.status
        elif link["resource"] == "funding_request":
            request = ctx.session.get(FundingRequest, link["id"])
            link["status"] = request.status
            for line in ctx.session.scalars(select(FundingLine).where(FundingLine.request_id == request.id)):
                establishes += [(line.payment_id, outcome) for outcome in FUNDING_OUTCOMES.get(line.status, ())]
    return ToolResult.ok(timeline, establishes=establishes)


@register(
    "get_policy", "read", ("payroll", "equity"), "read_scoped",
    """The policy in force: the authority table (which source to trust for each fact), the rules with their ids,
    contribution rates, materiality, the approval limit, and the policy hash to cite. Not for changing policy.""",
    NoInput,
)
def get_policy(ctx, args: NoInput) -> ToolResult:
    from app.reconcile.routes import policy

    return ToolResult.ok(policy())


@register(
    "get_employee_record", "read", ("payroll", "equity"), "read_scoped",
    """HR's record for the person on this case, as of the case month: entity, department, status, hire and
    termination dates, salary, and the bank account on the HR master month by month. HR is the authority for
    employment dates and pay destination. Only the person the case is about.""",
    PersonInput,
)
def get_employee_record(ctx, args: PersonInput) -> ToolResult:
    _require_person(ctx, args.employee_number)
    rows = list(
        ctx.session.scalars(
            select(HrCensusRow)
            .where(HrCensusRow.employee_number == args.employee_number, HrCensusRow.period <= ctx.period)
            .order_by(HrCensusRow.period)
        )
    )
    if not rows:
        return ToolResult.ok({"employee_number": args.employee_number, "in_hr": False}, establishes=[(args.employee_number, "not_found")])
    latest = rows[-1]
    return ToolResult.ok(
        {
            "employee_number": latest.employee_number,
            "in_hr": True,
            "legal_name": latest.legal_name,
            "entity_code": latest.entity_code,
            "department": latest.department,
            "status": latest.status,
            "hire_date": latest.hire_date,
            "termination_date": latest.termination_date,
            "termination_recorded_on": latest.termination_recorded_on,
            "annual_salary": money_str(latest.annual_salary),
            "currency": latest.currency,
            "bank_account_by_month": {row.period: row.bank_account for row in rows},
        },
        establishes=[(args.employee_number, "found")],
    )


@register(
    "get_register_lines", "read", ("payroll",), "read_scoped",
    """The payroll provider's register lines for the person on this case, every month up to the case month:
    gross, withholdings, net, employer tax and contribution, the account paid, and any retro pay. The register is
    what was paid, not what was authorized. Only the person the case is about.""",
    PersonInput,
)
def get_register_lines(ctx, args: PersonInput) -> ToolResult:
    _require_person(ctx, args.employee_number)
    rows = ctx.session.scalars(
        select(PayrollRegisterRow)
        .where(PayrollRegisterRow.employee_number == args.employee_number, PayrollRegisterRow.period <= ctx.period)
        .order_by(PayrollRegisterRow.period)
    )
    lines = [
        {
            "period": row.period,
            "entity_code": row.entity_code,
            "department": row.department,
            "pay_period": [row.pay_period_start, row.pay_period_end],
            "gross_wages": money_str(row.gross_wages),
            "employee_withholdings": money_str(row.employee_withholdings),
            "net_pay": money_str(row.net_pay),
            "employer_tax": money_str(row.employer_tax),
            "employer_contribution": money_str(row.employer_contribution),
            "currency": row.currency,
            "destination_account": row.destination_account,
            "retro_effective_date": row.retro_effective_date,
            "retro_gross": None if row.retro_gross is None else money_str(row.retro_gross),
        }
        for row in rows
    ]
    return ToolResult.ok({"employee_number": args.employee_number, "lines": lines}, establishes=[(args.employee_number, "found" if lines else "not_found")])


@register(
    "get_comp_actions", "read", ("payroll",), "read_scoped",
    """Approved compensation changes for the person on this case: effective date, new salary, who entered it and
    who approved it. The approved action is the authority for authorized pay. Only the person the case is about.""",
    PersonInput,
)
def get_comp_actions(ctx, args: PersonInput) -> ToolResult:
    _require_person(ctx, args.employee_number)
    actions = []
    for document in ctx.session.scalars(select(SourceDocument).where(SourceDocument.system == "comp_action")):
        actions += [{**row, "document": document.content_hash} for row in json.loads(document.body) if row["employee_number"] == args.employee_number]
    return ToolResult.ok({"employee_number": args.employee_number, "actions": actions})


@register(
    "get_signed_grant", "read", ("equity",), "read_scoped",
    """The signed grant and its board approval for a grant of the person on this case: grant date, cliff, vest
    end, shares, fair value, and whether the board approved it. The signed grant is the authority for grant terms;
    the equity platform is not.""",
    GrantInput,
)
def get_signed_grant(ctx, args: GrantInput) -> ToolResult:
    grant = next((row for row in signed_grants(ctx.session) if row.grant_number == args.grant_number), None)
    if grant is None:
        return ToolResult.ok({"grant_number": args.grant_number, "on_file": False}, establishes=[(args.grant_number, "not_found")])
    _require_person(ctx, grant.employee_number)
    return ToolResult.ok(
        {
            "grant_number": grant.grant_number,
            "on_file": True,
            "employee_number": grant.employee_number,
            "home_entity": grant.home_entity,
            "grant_date": grant.grant_date,
            "cliff_end": grant.cliff_end,
            "vest_end": grant.vest_end,
            "shares": grant.shares,
            "fair_value": money_str(grant.fair_value),
            "currency": grant.currency,
            "board_approved": grant.board_approved,
            "board_approval_date": grant.board_approval_date,
            "document": grant.content_hash,
        },
        establishes=[(grant.grant_number, "found")],
    )


@register(
    "get_equity_platform", "read", ("equity",), "read_scoped",
    """What the equity platform shows this month for the person on this case: each grant row, its schedule,
    status and the monthly expense it fed. A process system: check it against the signed grant, never trust it
    on its own.""",
    PersonInput,
)
def get_equity_platform(ctx, args: PersonInput) -> ToolResult:
    _require_person(ctx, args.employee_number)
    rows = ctx.session.scalars(
        select(EquitySnapshot).where(EquitySnapshot.employee_number == args.employee_number, EquitySnapshot.period == ctx.period)
    )
    grants = [
        {
            "grant_number": row.grant_number,
            "entity_code": row.entity_code,
            "department": row.department,
            "grant_date": row.grant_date,
            "cliff_end": row.cliff_end,
            "vest_end": row.vest_end,
            "shares": row.shares,
            "platform_status": row.platform_status,
            "board_approved": row.board_approved,
            "monthly_expense": money_str(row.monthly_expense),
            "currency": row.currency,
        }
        for row in rows
    ]
    return ToolResult.ok({"employee_number": args.employee_number, "grants": grants})


class LedgerInput(ToolInput):
    entity_code: Literal["US", "UK", "DE"]
    period: str
    booking_key_prefix: str | None


@register(
    "get_ledger_entries", "read", ("payroll", "equity"), "read_scoped",
    """Journal entries booked or drafted for an entity and month, with status (draft, approved, posted, rejected,
    withdrawn), memo, booking key and totals; optionally only keys starting with a prefix such as
    "payroll:overpayment:". The ledger is the authority for what was booked, never for HR facts or cash.""",
    LedgerInput,
)
def get_ledger_entries(ctx, args: LedgerInput) -> ToolResult:
    entries = [
        entry
        for entry in list_entries(ctx.session, args.entity_code, args.period)
        if args.booking_key_prefix is None or (entry.booking_key or "").startswith(args.booking_key_prefix)
    ]
    data = [
        {
            "number": entry.number,
            "status": entry.status,
            "memo": entry.memo,
            "booking_key": entry.booking_key,
            "total": money_str(sum((line.debit for line in entry.lines), ZERO)),
            "currency": entry.currency,
        }
        for entry in entries
    ]
    return ToolResult.ok({"entity_code": args.entity_code, "period": args.period, "entries": data}, establishes=_journal_establishes(entries))


class PaymentInput(ToolInput):
    payment_id: str


@register(
    "get_bank_transactions", "read", ("payroll",), "read_scoped",
    """What the bank shows for one payment line on this case: lines in the bank file and transfers the bank
    executed, with dates, amounts and accounts. The bank is the authority for cash that moved. Only payments to the
    person the case is about.""",
    PaymentInput,
)
def get_bank_transactions(ctx, args: PaymentInput) -> ToolResult:
    line = ctx.session.scalar(select(FundingInstruction).where(FundingInstruction.payment_id == args.payment_id))
    if line is None:
        return ToolResult.ok({"payment_id": args.payment_id, "known": False}, establishes=[(args.payment_id, "not_found")])
    _require_person(ctx, line.employee_number)
    bank = [
        {"source": "bank_file", "posted_on": row.posted_on, "amount": money_str(row.amount), "currency": row.currency, "account": row.destination_account}
        for row in ctx.session.scalars(select(BankLine).where(BankLine.payment_id == args.payment_id))
    ]
    bank += [
        {"source": "bank_transfer", "posted_on": row.executed_on, "amount": money_str(row.amount), "currency": row.currency, "account": row.destination_account}
        for row in ctx.session.scalars(select(BankTransfer).where(BankTransfer.payment_id == args.payment_id))
    ]
    return ToolResult.ok(
        {"payment_id": args.payment_id, "instructed": money_str(line.amount), "currency": line.currency, "bank": bank},
        establishes=[(args.payment_id, "paid" if bank else "not_found")],
    )


class DocumentInput(ToolInput):
    content_hash: str


@register(
    "get_document", "read", ("payroll", "equity"), "read_scoped",
    """The full text of a document this case's finding cites, by its SHA-256 hash (an email, a register export,
    a signed grant). Only documents the case rests on.""",
    DocumentInput,
)
def get_document(ctx, args: DocumentInput) -> ToolResult:
    document = _cited_document(ctx, args.content_hash)
    return ToolResult.ok({"title": document.title, "system": document.system, "received_on": document.received_on, "body": document.body, "content_hash": document.content_hash})


def _cited_document(ctx, content_hash: str) -> SourceDocument:
    finding = ctx.session.get(Reconciliation, ctx.case.finding_id)
    cited = {item.get("doc_sha256") for item in json.loads(finding.basis_json) if item.get("type") == "document"}
    if content_hash not in cited:
        enforce(deny("PRV-1", f"Case {ctx.case.key} does not cite that document."), ctx.seat, "read_scoped")
    return ctx.session.scalar(select(SourceDocument).where(SourceDocument.content_hash == content_hash))


@register(
    "extract_document", "read", ("payroll", "equity"), "read_scoped",
    """Read a document this case cites (an email, a note) into fields, each with its value and the line it came
    from. Code checks every value against the document text and refuses the extraction if any value is not
    there. Use it to read noisy text; an email is still never a source for bank details.""",
    DocumentInput,
)
def extract_document(ctx, args: DocumentInput) -> ToolResult:
    from app.agent.extract import ExtractionRefused, extraction_problems, reader

    document = _cited_document(ctx, args.content_hash)
    from app.agent.model import ModelCallError

    try:
        extraction = reader().extract(document.title, document.body)
    except ExtractionRefused as exc:
        return ToolResult.failed(f"extraction_declined: {exc}")
    except ModelCallError as exc:
        return ToolResult.failed(f"extraction_failed: {exc}")
    problems = extraction_problems(document.body, extraction)
    if problems:
        return ToolResult.refused(None, "Extraction refused; it states what the document does not: " + " ".join(problems))
    return ToolResult.ok(
        {"title": document.title, "content_hash": document.content_hash, **extraction.model_dump()},
        establishes=[(document.content_hash, "found")],
    )


class PrecedentInput(ToolInput):
    rule: str | None


@register(
    "search_precedents", "read", ("payroll", "equity"), "read_scoped",
    """Rulings in force today that match this case's person and entity, optionally for one rule id (e.g. BNK-1).
    A precedent is how an earlier decision changes this one; cite its id. Not for making a new ruling.""",
    PrecedentInput,
)
def search_precedents_tool(ctx, args: PrecedentInput) -> ToolResult:
    people = sorted(_people(ctx))
    rows = search_precedents(ctx.session, today(ctx.session), rule=args.rule, employee=people[0] if people else None, entity=ctx.case.entity_code)
    return ToolResult.ok({"on": today(ctx.session), "precedents": [precedent_dict(row) for row in rows]}, establishes=[(row.id, "found") for row in rows])


@register(
    "get_employee_history", "read", ("payroll", "equity"), "read_scoped",
    """The memory of the person on this case, from every loaded month (four years and more): entity and department
    over time, HR salary over time, approved pay changes, pay received month by month, payments and whether the
    bank cleared them, bank accounts over time, call-backs, earlier cases and the rulings that cover them. Use it to
    see whether something is new or has been true for years. Only the person the case is about.""",
    PersonInput,
)
def get_employee_history(ctx, args: PersonInput) -> ToolResult:
    from app.memory.history import employee_timeline

    _require_person(ctx, args.employee_number)
    timeline = employee_timeline(ctx.session, args.employee_number, ctx.period)
    if timeline is None:
        return ToolResult.ok({"employee_number": args.employee_number, "found": False}, establishes=[(args.employee_number, "not_found")])
    return ToolResult.ok(timeline, establishes=[(args.employee_number, "found")])


class DepartmentInput(ToolInput):
    entity_code: Literal["US", "UK", "DE"]
    department: Literal["Engineering", "Product", "GTM", "G&A"]


@register(
    "get_department_history", "read", ("payroll", "equity"), "read_scoped",
    """What is normal in a department, learned from the last four years: headcount by month, the monthly gross pay
    band and the employer contribution rate, with the observation Vestly recorded (its months and evidence row).
    Totals only; months with fewer than three people are hidden. Not for one person's pay.""",
    DepartmentInput,
)
def get_department_history(ctx, args: DepartmentInput) -> ToolResult:
    from app.memory.history import department_baseline, observation_dict
    from app.memory.models import Observation

    baseline = department_baseline(ctx.session, args.entity_code, args.department, ctx.period)
    learned = ctx.session.scalar(
        select(Observation)
        .where(Observation.kind == "department_baseline", Observation.subject == f"{args.entity_code}:{args.department}")
        .order_by(Observation.as_of.desc(), Observation.learned_at.desc())
        .limit(1)
    )
    subject = f"{args.entity_code}:{args.department}"
    return ToolResult.ok(
        {**baseline, "observation": None if learned is None else observation_dict(learned)},
        establishes=[(subject, "found" if baseline["months"] else "not_found")],
        partial=bool(baseline["months_hidden_small_group"]),
    )


# ---------------------------------------------------------------- compute


@register(
    "match_register", "compute", ("payroll",), "compute",
    """Compare the person's register line this month with HR and approved pay: entity, department, gross against
    HR's monthly salary, and the account paid against the HR bank master. Returns the differences found by code.""",
    PersonInput,
)
def match_register(ctx, args: PersonInput) -> ToolResult:
    _require_person(ctx, args.employee_number)
    row = ctx.session.scalar(select(PayrollRegisterRow).where(PayrollRegisterRow.employee_number == args.employee_number, PayrollRegisterRow.period == ctx.period))
    hr = ctx.session.scalar(select(HrCensusRow).where(HrCensusRow.employee_number == args.employee_number, HrCensusRow.period == ctx.period))
    if row is None:
        return ToolResult.ok({"employee_number": args.employee_number, "register_line": False, "differences": []}, establishes=[(args.employee_number, "not_found")])
    if hr is None:
        return ToolResult.ok({"employee_number": args.employee_number, "in_hr": False, "differences": ["paid but not in HR"]}, establishes=[(args.employee_number, "found")])
    monthly = (hr.annual_salary / 12).quantize(CENT)
    differences = []
    if row.entity_code != hr.entity_code:
        differences.append(f"paid by {row.entity_code}; HR has {hr.entity_code}")
    if row.department != hr.department:
        differences.append(f"department {row.department}; HR has {hr.department}")
    if row.gross_wages != monthly:
        differences.append(f"gross {money_str(row.gross_wages)}; HR monthly salary {money_str(monthly)}")
    if row.destination_account != hr.bank_account:
        differences.append(f"paid to {row.destination_account}; HR bank master {hr.bank_account}")
    return ToolResult.ok({"employee_number": args.employee_number, "differences": differences}, establishes=[(args.employee_number, "found")])


class EntityInput(ToolInput):
    entity_code: Literal["US", "UK", "DE"]


@register(
    "compute_contributions", "compute", ("payroll",), "compute",
    """Employer contributions for an entity this month: policy rate times gross for every register line, against
    what the register computed, to the cent (CTR-1). Returns totals and the line of the person on this case.""",
    EntityInput,
)
def compute_contributions(ctx, args: EntityInput) -> ToolResult:
    rate = load_policy().contribution_rate(args.entity_code)
    rows = list(ctx.session.scalars(select(PayrollRegisterRow).where(PayrollRegisterRow.entity_code == args.entity_code, PayrollRegisterRow.period == ctx.period)))
    expected = sum(((row.gross_wages * rate).quantize(CENT) for row in rows), ZERO)
    booked = sum((row.employer_contribution for row in rows), ZERO)
    mine = [row for row in rows if row.employee_number in _people(ctx)]
    person = None
    if mine:
        row = mine[0]
        person = {
            "employee_number": row.employee_number,
            "gross": money_str(row.gross_wages),
            "register": money_str(row.employer_contribution),
            "policy": money_str((row.gross_wages * rate).quantize(CENT)),
            "difference": money_str((row.gross_wages * rate).quantize(CENT) - row.employer_contribution),
        }
    return ToolResult.ok(
        {"entity_code": args.entity_code, "rate": str(rate), "policy_total": money_str(expected), "register_total": money_str(booked), "difference": money_str(expected - booked), "case_person": person}
    )


@register(
    "compute_sbc_expense", "compute", ("equity",), "compute",
    """Stock expense for the grants of the person on this case: what the subledger earned against what the platform
    fed the ledger since the last true-up, with the difference and its reason (forfeiture, missing approval,
    duplicate, wrong entity). Computed by code; never estimate it yourself.""",
    NoInput,
)
def compute_sbc_expense(ctx, args: NoInput) -> ToolResult:
    people = _people(ctx)
    rows = [row for row in trueup_breakdown(ctx.session, ctx.period) if row["employee_number"] in people]
    return ToolResult.ok({"period": ctx.period, "grants": rows}, establishes=[(row["grant_number"], "found") for row in rows])


class KeyInput(ToolInput):
    key: str


@register(
    "check_duplicate", "compute", ("payroll", "equity"), "compute",
    """Is this grant or payment key a duplicate of one already on file? Keys are compared after normalizing case,
    separators, leading zeros and copy suffixes such as -DUP. Required before reject_duplicate.""",
    KeyInput,
)
def check_duplicate(ctx, args: KeyInput) -> ToolResult:
    wanted = normalize_key(args.key)
    grants = set(ctx.session.scalars(select(EquitySnapshot.grant_number).where(EquitySnapshot.period == ctx.period)))
    grants |= {row.grant_number for row in signed_grants(ctx.session)}
    payments = set(ctx.session.scalars(select(FundingInstruction.payment_id).where(FundingInstruction.period == ctx.period)))
    others = sorted(key for key in grants | payments if key != args.key and normalize_key(key) == wanted)
    return ToolResult.ok(
        {"key": args.key, "normalized": wanted, "duplicate_of": others[0] if others else None, "matches": others},
        establishes=[(args.key, "found")] if others else [(args.key, "not_found")],
    )


@register(
    "reconcile_funding", "compute", ("payroll",), "compute",
    """Payment file against the bank for an entity this month: which payment ids cleared, which are missing, and
    which went to an account that is not the HR bank master. Payment ids and amounts only.""",
    EntityInput,
)
def reconcile_funding(ctx, args: EntityInput) -> ToolResult:
    cleared, missing, diverted = [], [], []
    establishes = []
    for line in ctx.session.scalars(select(FundingInstruction).where(FundingInstruction.entity_code == args.entity_code, FundingInstruction.period == ctx.period).order_by(FundingInstruction.payment_id)):
        paid = ctx.session.scalar(select(BankLine).where(BankLine.payment_id == line.payment_id).limit(1)) or ctx.session.scalar(
            select(BankTransfer).where(BankTransfer.idempotency_key == line.payment_id)
        )
        master = ctx.session.scalar(select(HrCensusRow.bank_account).where(HrCensusRow.employee_number == line.employee_number, HrCensusRow.period == ctx.period).limit(1))
        item = {"payment_id": line.payment_id, "amount": money_str(line.amount)}
        (cleared if paid else missing).append(item)
        establishes.append((line.payment_id, "paid" if paid else "not_reconciled"))
        if master is not None and master != line.destination_account:
            diverted.append(item)
    establishes.append((args.entity_code, "not_reconciled" if missing else "reconciled"))
    return ToolResult.ok({"entity_code": args.entity_code, "cleared": cleared, "missing": missing, "not_to_hr_master": diverted}, establishes=establishes)


class ConvertInput(ToolInput):
    amount: str
    from_currency: Literal["USD", "GBP", "EUR"]
    to_currency: Literal["USD", "GBP", "EUR"]


@register(
    "convert_currency", "compute", ("payroll", "equity"), "compute",
    """Translate an amount at the case month's reference rate: Open Exchange Rates' end-of-day rate for the
    month's last day (pay day). If that rate is not stored it falls back to the latest earlier day and says so in
    `fallback` with the rate date: report that date. A fallback rate is for sizing and reporting only; nothing is
    ever booked at it.""",
    ConvertInput,
)
def convert_currency(ctx, args: ConvertInput) -> ToolResult:
    try:
        amount = Decimal(args.amount)
    except InvalidOperation:
        return ToolResult.failed(f"invalid_amount: {args.amount!r} is not a number.")
    from app.fx.store import latest_before, month_end, reference

    rate, rate_date = (None, None) if ctx.period in FX_FEED_DOWN else _rate(rate_card_for(ctx.session, ctx.period) or {}, args.from_currency, args.to_currency)
    fallback = None
    if rate is None:
        earlier = latest_before(ctx.session, month_end(ctx.period))
        if earlier is not None:
            rate, rate_date = _rate(reference(earlier), args.from_currency, args.to_currency)
            reason = "the reference-rate feed is down" if ctx.period in FX_FEED_DOWN else f"no end-of-day rate for {month_end(ctx.period)} is stored yet"
            fallback = {"rate": str(rate), "rate_date": rate_date, "reason": reason}
    if rate is None:
        return ToolResult.failed(f"no_reference_rate: no {args.from_currency}->{args.to_currency} rate for {ctx.period} or any earlier day.")
    converted = (amount * rate).quantize(CENT)
    label = f"{money_str(amount)} {args.from_currency}"
    return ToolResult.ok(
        {"amount": money_str(amount), "from": args.from_currency, "to": args.to_currency, "rate": str(rate), "rate_date": rate_date, "converted": money_str(converted), "source": "Open Exchange Rates"},
        establishes=[(label, "converted")],
        fallback=fallback,
    )


def _rate(card: dict, source: str, target: str) -> tuple[Decimal | None, str | None]:
    to_usd, target_usd = rate_to(card, source), rate_to(card, target)
    if to_usd is None or target_usd is None:
        return None, None
    return (to_usd / target_usd).quantize(Decimal("0.000001")), card.get("rate_date")


@register(
    "run_tests", "compute", ("payroll", "equity"), "compute",
    """Run Vestly's test suite with a fixed command (python -m pytest -q in the backend). Returns the exit code,
    passed, failed, skipped and error counts, and the end of the output. The exit code alone decides: status ok
    only for exit code 0. Report skipped tests. Takes no arguments.""",
    NoInput,
)
def run_tests(ctx, args: NoInput) -> ToolResult:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    try:
        done = subprocess.run(command, cwd=TEST_WORKSPACE, capture_output=True, text=True, timeout=TEST_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        tail = ((exc.stdout or "") if isinstance(exc.stdout, str) else "").splitlines()[-60:]
        return ToolResult.failed(f"timeout: the test run did not finish in {TEST_TIMEOUT_SECONDS} seconds.", {"exit_code": None, "output_tail": tail}, establishes=[("test_suite", "tests_failed")])
    output = (done.stdout or "") + (done.stderr or "")
    lines = output.splitlines()
    summary = next((line for line in reversed(lines) if SUMMARY_COUNT.search(line)), "")
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for number, word in SUMMARY_COUNT.findall(summary):
        key = "errors" if word.startswith("error") else word
        if key in counts:
            counts[key] = int(number)
    data = {"command": "python -m pytest -q", "exit_code": done.returncode, **counts, "summary": summary.strip("= ").strip(), "output_tail": lines[-60:]}
    if done.returncode == 0:
        return ToolResult.ok(data, establishes=[("test_suite", "tests_passed")])
    return ToolResult.failed(f"pytest exited with code {done.returncode}: {data['summary'] or 'no summary line'}", data, establishes=[("test_suite", "tests_failed")])


# ---------------------------------------------------------------- gated


class ProposeInput(ToolInput):
    draft: Literal["payroll_close", "equity_trueup"]


@register(
    "propose_entry", "gated", ("payroll", "equity"), "propose_entry",
    """Draft this month's journals from the deterministic services: payroll_close (payroll seat: wages,
    accruals, receivables, suspense) or equity_trueup (equity seat: stock expense true-up). Amounts come from code,
    never from you. Every draft goes to the approvals inbox, so the result is pending: it is not approved and not
    posted. Never posts.""",
    ProposeInput,
)
def propose_entry(ctx, args: ProposeInput) -> ToolResult:
    from app.equity.service import draft_equity
    from app.payroll.service import draft_payroll

    drafted = (draft_payroll if args.draft == "payroll_close" else draft_equity)(ctx.session, ctx.period, ctx.seat)
    cases.link_drafts(ctx.session, ctx.period)
    journals = drafted["journals"]
    data = {
        "journals": [{"number": entry.number, "booking_key": entry.booking_key, "status": entry.status, "total": money_str(sum((line.debit for line in entry.lines), ZERO))} for entry in journals],
        "already_on_file": drafted["already_on_file"],
        "held": drafted.get("held", []),
    }
    if not journals:
        return ToolResult.ok(data)
    return ToolResult.pending(data, establishes=_journal_establishes(journals))


class FundingInput(ToolInput):
    payment_ids: list[str]
    reason: str


@register(
    "request_funding", "gated", ("payroll",), "request_funding",
    """Ask treasury to send named payment lines from the payment file, each under its payment id as the
    idempotency key. Amounts and accounts come from the payment file. Refused for an account without a verified
    call-back (BNK-1) or a line already paid or requested (DUP-1). The result is pending until treasury releases
    it; it is not paid. Never pays.""",
    FundingInput,
)
def request_funding(ctx, args: FundingInput) -> ToolResult:
    from app.controls.posting import as_dict, request_funding as request

    row = request(ctx.session, ctx.seat, ctx.period, args.payment_ids, args.reason, [ctx.case.id])
    data = as_dict(ctx.session, row)
    return ToolResult.pending(data, establishes=[(line["payment_id"], outcome) for line in data["lines"] for outcome in ("requested", "pending_approval")])


class ReasonInput(ToolInput):
    reason: str


@register(
    "hold_case", "gated", ("payroll", "equity"), "hold_case",
    """Hold this case: nothing moves for it until a person answers. Use when the evidence conflicts and no rule
    or precedent settles it. Always allowed; never releases anything.""",
    ReasonInput,
)
def hold_case(ctx, args: ReasonInput) -> ToolResult:
    case = ctx.case
    if case.state in cases.FINAL:
        return ToolResult.refused(None, f"Case {case.key} is {case.state}; it cannot be held.")
    if case.outcome != "held":
        target = "held" if "held" in cases.TRANSITIONS[case.state] else case.state
        cases.move(ctx.session, case, target, ctx.seat.handle, f"Held by {ctx.seat.handle}: {args.reason}", "held")
        ctx.session.commit()
    return ToolResult.ok({"case": case.key, "state": case.state, "outcome": case.outcome}, establishes=[(case.subject_number, "held")])


@register(
    "reject_duplicate", "gated", ("payroll", "equity"), "reject_duplicate",
    """Reject this case's item as a duplicate of something already on file. Allowed only after check_duplicate in
    this run showed the duplicate. Books nothing.""",
    KeyInput,
)
def reject_duplicate(ctx, args: KeyInput) -> ToolResult:
    shown = [
        json.loads(row.payload_json)
        for row in ctx.run_events("check_duplicate")
        if json.loads(row.payload_json)["args"].get("key") == args.key
    ]
    if not any(item["status"] == "ok" and item["data"].get("duplicate_of") for item in shown):
        return ToolResult.refused(None, f"No check_duplicate result in this run shows {args.key} as a duplicate.")
    case = ctx.case
    if case.state in cases.FINAL:
        return ToolResult.refused(None, f"Case {case.key} is {case.state}.")
    if case.outcome != "rejected_duplicate":
        target = "rejected_duplicate" if "rejected_duplicate" in cases.TRANSITIONS[case.state] else case.state
        cases.move(ctx.session, case, target, ctx.seat.handle, f"Rejected as a duplicate of {shown[-1]['data']['duplicate_of']}.", "rejected_duplicate")
        ctx.session.commit()
    return ToolResult.ok({"case": case.key, "duplicate_of": shown[-1]["data"]["duplicate_of"]}, establishes=[(case.subject_number, "rejected_duplicate")])


class QuestionInput(ToolInput):
    question: str


def _ask(ctx, handles: list[str], text: str) -> ToolResult:
    case = ctx.case
    if case.state in cases.FINAL:
        return ToolResult.refused(None, f"Case {case.key} is {case.state}; there is nothing to ask.")
    asked = [cases.ask(ctx.session, case, handle, ctx.seat.handle, text) for handle in handles]
    if case.state != "waiting_on_human":
        cases.move(ctx.session, case, "waiting_on_human", ctx.seat.handle, f"Asked {', '.join(handles)}.")
    ctx.session.commit()
    return ToolResult.pending(
        {"questions": [{"id": row.id, "asked_to": row.asked_to, "due_on": row.due_on} for row in asked]},
        establishes=[(handle, "asked") for handle in handles],
    )


def _owners(ctx) -> list[str]:
    finding = ctx.session.get(Reconciliation, ctx.case.finding_id)
    return json.loads(finding.ask_json)


@register(
    "ask_human", "gated", ("payroll", "equity"), "ask_human",
    """Put a question to the owner of this case's facts. Code picks the owner from the policy's routing map; you
    write the question, with the context they need. The result is pending: the answer has not arrived.""",
    QuestionInput,
)
def ask_human(ctx, args: QuestionInput) -> ToolResult:
    owners = _owners(ctx)
    if not owners:
        return ToolResult.refused(None, "Nobody owns this kind of question in the routing map.")
    return _ask(ctx, owners, args.question)


class DocumentRequestInput(ToolInput):
    document: str
    reason: str


@register(
    "request_document", "gated", ("payroll", "equity"), "request_document",
    """Ask the owner of this case's facts (HR, legal, the provider; picked by code) for a record or a corrected
    export. Pending until they answer.""",
    DocumentRequestInput,
)
def request_document(ctx, args: DocumentRequestInput) -> ToolResult:
    owners = _owners(ctx)
    if not owners:
        return ToolResult.refused(None, "Nobody owns this kind of question in the routing map.")
    return _ask(ctx, owners, f"Please send: {args.document}. Why: {args.reason}")


class ReopenInput(ToolInput):
    entity_code: Literal["US", "UK", "DE"]
    period: str
    reason: str


@register(
    "request_reopen", "gated", ("payroll", "equity"), "request_reopen",
    """Ask the controller to reopen a locked month, with the reason. Only the controller reopens; this never
    does. Prefer booking a correction once in the first open month.""",
    ReopenInput,
)
def request_reopen(ctx, args: ReopenInput) -> ToolResult:
    row = find_period(ctx.session, require_entity(ctx.session, args.entity_code), args.period)
    if row is None or row.status != "locked":
        return ToolResult.failed(f"not_locked: {args.entity_code} {args.period} is {'not opened' if row is None else row.status}, not locked.")
    record(ctx.session, ctx.seat.handle, "reopen_requested", "period", f"{args.entity_code}:{args.period}", case_id=ctx.case.id, payload={"reason": args.reason})
    return _ask(ctx, [CONTROLLER_HANDLE], f"Reopen request for {args.entity_code} {args.period}: {args.reason}")


class PrecedentProposalInput(ToolInput):
    rule: str
    scope_entity: Literal["US", "UK", "DE"] | None
    scope_employee: str | None
    ruling: str


@register(
    "propose_precedent", "gated", ("payroll", "equity"), "propose_precedent",
    """Suggest that a ruling on this case become a precedent, with its rule id and scope (an entity, an employee,
    or neither for the whole rule). Only the controller makes precedents; this asks them.""",
    PrecedentProposalInput,
)
def propose_precedent(ctx, args: PrecedentProposalInput) -> ToolResult:
    import re as _re

    from app.ledger.models import new_id
    from app.record.models import PrecedentProposal

    if not _re.match(r"^[A-Z]{3}-\d+$", args.rule) or args.rule not in dict(load_policy().rules):
        return ToolResult.refused(None, f"{args.rule} is not a rule in the policy.")
    scope = {key: value for key, value in (("entity", args.scope_entity), ("employee", args.scope_employee)) if value}
    result = _ask(ctx, [CONTROLLER_HANDLE], f"Proposed precedent under {args.rule}, scope {scope or 'the whole rule'}: {args.ruling} Confirm to make it a precedent, or decline.")
    if result.status != "pending":
        return result
    question_id = result.data["questions"][0]["id"]
    ctx.session.add(
        PrecedentProposal(
            id=new_id(), case_id=ctx.case.id, question_id=question_id, rule=args.rule, scope_json=json.dumps(scope, sort_keys=True),
            ruling=args.ruling, proposed_by=ctx.seat.handle, status="proposed",
        )
    )
    ctx.session.commit()
    return result

