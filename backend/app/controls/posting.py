"""The posting service: money moves once, and only after a person approved exactly that.

Funding: Vestly asks for a batch of payment lines (`request_funding`). The gate checks each
line first: the account must be the HR bank master or carry a confirmed call-back (BNK-1),
and a line the bank already paid, or another live request already carries, is refused
(DUP-1). Treasury releases it through the approvals inbox. The bank is told to pay each
line under its idempotency key (the payment id), so sending a line twice never pays it
twice. A batch that timed out is re-sent line by line, only for lines the bank has not
confirmed. Each confirmed line gets a cash journal (debit 2100 net pay payable, credit 1000
cash) drafted by Vestly, so the funding log, the bank and the ledger agree to the cent.

Journals: an approved entry posts once; posting it again changes nothing.

The bank is simulated (there is no sponsor API). It keeps its own append-only record of
what it executed, and tests can make it drop a send or lose its reply.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identities import PAYROLL_SEAT, Actor
from app.controls.callbacks import check_pay_destination
from app.controls.clock import today
from app.controls.gate import check_role, deny, enforce
from app.controls.models import BankTransfer, FundingLine, FundingRequest
from app.ledger.errors import LedgerError
from app.ledger.models import JournalEntry, JournalLine
from app.ledger.money import money_str
from app.ledger.service import draft_entry, live_entry_for_key, post_approved, require_entry
from app.record.evidence import record
from app.world.models import BankLine, FundingInstruction, SourceDocument

SERVICE = "posting.service"
LIVE_LINE = ("requested", "sent", "confirmed")
ZERO = Decimal("0.00")


# ---------------------------------------------------------------- the simulated bank


class BankTimeout(Exception):
    """The bank did not answer. It may or may not have executed the payment."""


# idempotency key -> "drop" (never executed) or "lost_reply" (executed, reply lost). Used once, then cleared.
BANK_FAULTS: dict[str, str] = {}


def bank_execute(session: Session, line: FundingLine, entity_code: str, by: str) -> BankTransfer:
    existing = bank_transfer(session, line.idempotency_key)
    if existing is not None:
        return existing
    fault = BANK_FAULTS.pop(line.idempotency_key, None)
    if fault == "drop":
        raise BankTimeout(line.idempotency_key)
    transfer = BankTransfer(
        idempotency_key=line.idempotency_key,
        payment_id=line.payment_id,
        entity_code=entity_code,
        amount=line.amount,
        currency=line.currency,
        destination_account=line.destination_account,
        executed_on=today(session),
        instructed_by=by,
    )
    session.add(transfer)
    session.flush()
    if fault == "lost_reply":
        raise BankTimeout(line.idempotency_key)
    return transfer


def bank_transfer(session: Session, idempotency_key: str) -> BankTransfer | None:
    return session.scalar(select(BankTransfer).where(BankTransfer.idempotency_key == idempotency_key))


def cleared_by_bank_file(session: Session, payment_id: str) -> BankLine | None:
    return session.scalar(select(BankLine).where(BankLine.payment_id == payment_id).limit(1))


# ---------------------------------------------------------------- asking for money


def request_funding(
    session: Session,
    actor: Actor,
    period: str,
    payment_ids: list[str],
    reason: str,
    case_ids: list[str] = (),
) -> FundingRequest:
    """Vestly asks treasury to send these payments. Amounts and accounts come from the payment file, never the caller."""
    from app.cases.models import Case
    from app.cases.service import link
    from app.controls.approvals import open_funding_request

    action = "request_funding"
    enforce(check_role(actor, action), actor, action)
    if not payment_ids:
        raise LedgerError("invalid_request", "Name at least one payment.", 422)
    instructions = []
    for payment_id in sorted(set(payment_ids)):
        row = session.scalar(
            select(FundingInstruction).where(FundingInstruction.payment_id == payment_id, FundingInstruction.period == period)
        )
        if row is None:
            raise LedgerError("unknown_payment", f"No {period} payment {payment_id} in the payment file.", 404)
        instructions.append(row)
    entities = {row.entity_code for row in instructions}
    if len(entities) != 1:
        raise LedgerError("invalid_request", "One funding request covers one entity's payments.", 422)
    on = today(session)
    for row in instructions:
        if cleared_by_bank_file(session, row.payment_id) or bank_transfer(session, row.payment_id):
            enforce(deny("DUP-1", f"{row.payment_id} has already cleared at the bank."), actor, action)
        live = session.scalar(
            select(FundingLine)
            .join(FundingRequest, FundingRequest.id == FundingLine.request_id)
            .where(FundingLine.payment_id == row.payment_id, FundingLine.status.in_(LIVE_LINE), FundingRequest.status.not_in(("declined", "voided")))
        )
        if live is not None:
            enforce(deny("DUP-1", f"{row.payment_id} is already in funding request {live.request_id}."), actor, action)
        enforce(check_pay_destination(session, row.employee_number, row.destination_account, period, on), actor, action)
    request = FundingRequest(
        period=period,
        entity_code=instructions[0].entity_code,
        currency=instructions[0].currency,
        requested_by=actor.handle,
        requested_on=on,
        reason=reason[:500],
        status="pending",
    )
    session.add(request)
    session.flush()
    for row in instructions:
        session.add(
            FundingLine(
                request_id=request.id,
                payment_id=row.payment_id,
                idempotency_key=row.payment_id,
                employee_number=row.employee_number,
                legal_name=row.legal_name,
                amount=row.amount,
                currency=row.currency,
                destination_account=row.destination_account,
                status="requested",
                sends=0,
            )
        )
    session.flush()
    documents = {session.get(SourceDocument, row.document_id).content_hash for row in instructions}
    record(
        session,
        actor.handle,
        "funding_requested",
        "funding_request",
        request.id,
        case_id=case_ids[0] if case_ids else None,
        payload={
            "entity": request.entity_code,
            "payments": [
                {"payment_id": row.payment_id, "amount": money_str(row.amount), "account": row.destination_account}
                for row in instructions
            ],
            "total": money_str(sum((row.amount for row in instructions), ZERO)),
            "currency": request.currency,
            "reason": request.reason,
            "cases": list(case_ids),
        },
        basis=[{"type": "document", "doc_sha256": digest} for digest in sorted(documents)],
        authority_row="cash_moved",
        sim_at=on,
    )
    for case_id in case_ids:
        link(session, session.get(Case, case_id), "funding_request", request.id)
    open_funding_request(session, request, actor.handle, list(case_ids))
    session.commit()
    return request


def lines_of(session: Session, request: FundingRequest) -> list[FundingLine]:
    return list(session.scalars(select(FundingLine).where(FundingLine.request_id == request.id).order_by(FundingLine.payment_id)))


# ---------------------------------------------------------------- sending, confirming, re-sending


def release(session: Session, request: FundingRequest, released_by: str) -> dict:
    """Treasury's approval is complete: tell the bank to pay every line it has not paid yet."""
    request.status = "released"
    session.flush()
    record(
        session,
        released_by,
        "funding_released",
        "funding_request",
        request.id,
        payload={"payments": [row.payment_id for row in lines_of(session, request)]},
        sim_at=today(session),
    )
    return send_unconfirmed(session, request, released_by)


def send_unconfirmed(session: Session, request: FundingRequest, by: str) -> dict:
    """Send each line without a bank confirmation, under its own idempotency key. Confirmed lines are left alone."""
    if request.status not in ("released", "confirmed"):
        raise LedgerError("not_released", f"Funding request {request.id} is {request.status}, not released.", 409)
    sent, timed_out = [], []
    for line in lines_of(session, request):
        if line.status not in ("requested", "sent"):
            continue
        if _confirmation(session, line) is not None:
            continue
        line.sends += 1
        line.status = "sent"
        session.flush()
        try:
            bank_execute(session, line, request.entity_code, by)
            sent.append(line.payment_id)
        except BankTimeout:
            timed_out.append(line.payment_id)
        record(
            session,
            by,
            "funding_sent",
            "funding_line",
            line.id,
            payload={
                "payment_id": line.payment_id,
                "idempotency_key": line.idempotency_key,
                "attempt": line.sends,
                "amount": money_str(line.amount),
                "account": line.destination_account,
                "bank": "timeout" if line.payment_id in timed_out else "accepted",
            },
            sim_at=today(session),
        )
    confirmed = confirm(session, request)
    session.commit()
    return {"sent": sent, "timed_out": timed_out, "confirmed": confirmed}


def _confirmation(session: Session, line: FundingLine) -> BankTransfer | BankLine | None:
    return bank_transfer(session, line.idempotency_key) or cleared_by_bank_file(session, line.payment_id)


def confirm(session: Session, request: FundingRequest) -> list[str]:
    """Mark the lines the bank shows as paid, and draft the cash journal for each one."""
    newly = []
    for line in lines_of(session, request):
        if line.status != "sent":
            continue
        proof = _confirmation(session, line)
        if proof is None:
            continue
        line.status = "confirmed"
        session.flush()
        record(
            session,
            SERVICE,
            "funding_confirmed",
            "funding_line",
            line.id,
            payload={"payment_id": line.payment_id, "bank_amount": money_str(proof.amount), "bank_record": proof.id},
            authority_row="cash_moved",
            sim_at=today(session),
        )
        newly.append(line.payment_id)
        _draft_cash_journal(session, request, line, proof)
    live = [row for row in lines_of(session, request) if row.status != "voided"]
    if live and all(row.status == "confirmed" for row in live) and request.status != "confirmed":
        request.status = "confirmed"
        session.flush()
        from app.cases.service import cases_linked_to, refresh_posted

        for case in cases_linked_to(session, "funding_request", request.id):
            refresh_posted(session, case)
    return newly


def _draft_cash_journal(session: Session, request: FundingRequest, line: FundingLine, proof) -> JournalEntry | None:
    """Vestly books the cash that the bank confirmed: debit net pay payable, credit cash, once per payment."""
    from app.cases.service import cases_linked_to, link

    key = f"funding:cash:{line.payment_id}"
    if live_entry_for_key(session, key) is not None:
        return None
    cases = cases_linked_to(session, "funding_request", request.id)
    case = next((row for row in cases if row.subject_number == line.payment_id), cases[0] if cases else None)
    sources = [{"table": "funding_lines", "id": line.id}, {"table": type(proof).__tablename__, "id": proof.id}]
    entry = draft_entry(
        session,
        {
            "entity_code": request.entity_code,
            "period": request.period,
            "memo": f"[Funding] Net pay to {line.legal_name}, {line.payment_id}, confirmed by the bank",
            "booking_key": key,
            "provenance": {"documents": [], "case_id": None if case is None else case.id, "funding_request": request.id},
            "lines": [
                {"account_code": "2100", "debit": money_str(proof.amount), "credit": "0.00", "sources": sources},
                {"account_code": "1000", "debit": "0.00", "credit": money_str(proof.amount), "sources": sources},
            ],
        },
        Actor(PAYROLL_SEAT, "agent"),
    )
    if case is not None:
        link(session, case, "journal_entry", entry.id)
    return entry


def resume_funding(session: Session) -> dict:
    """After a restart or a timeout: confirm what the bank paid, and re-send only what it did not."""
    resent: list[str] = []
    for request in list(session.scalars(select(FundingRequest).where(FundingRequest.status == "released"))):
        result = send_unconfirmed(session, request, SERVICE)
        resent += result["sent"] + result["timed_out"]
    return {"funding_resent": sorted(resent)}


def decline_funding(session: Session, request: FundingRequest) -> None:
    request.status = "declined"
    for line in lines_of(session, request):
        if line.status == "requested":
            line.status = "voided"
    session.flush()


def funding_binding(session: Session, request: FundingRequest, case_ids: list[str]) -> dict:
    """What treasury agrees to: every payee, account and amount, the entity, the period and the cases."""
    return {
        "kind": "funding",
        "request_id": request.id,
        "entity": request.entity_code,
        "period": request.period,
        "currency": request.currency,
        "cases": sorted(case_ids),
        "lines": [
            [row.payment_id, row.employee_number, row.destination_account, money_str(row.amount)]
            for row in lines_of(session, request)
            if row.status != "voided"
        ],
    }


# ---------------------------------------------------------------- journals and the reconciliation


def post_entry(session: Session, entry_id: str, actor: Actor) -> tuple[JournalEntry, bool]:
    """Post an approved entry once. Posting it again returns it unchanged: (entry, posted_now)."""
    from app.cases.service import on_entry_posted

    entry = require_entry(session, entry_id)
    if entry.status == "posted":
        return entry, False
    entry = post_approved(session, entry_id, actor)
    on_entry_posted(session, entry.id)
    return entry, True


def reconcile_funding(session: Session, request: FundingRequest) -> dict:
    """Per payment: what was asked, what the bank paid, what the ledger booked as cash. They must agree."""
    rows = []
    for line in lines_of(session, request):
        proof = _confirmation(session, line)
        cash = live_entry_for_key(session, f"funding:cash:{line.payment_id}")
        booked = None
        if cash is not None and cash.status == "posted":
            booked = sum(
                (row.credit for row in session.scalars(select(JournalLine).where(JournalLine.entry_id == cash.id)) if row.credit > 0),
                ZERO,
            )
        bank = None if proof is None else proof.amount
        rows.append(
            {
                "payment_id": line.payment_id,
                "status": line.status,
                "requested": money_str(line.amount),
                "bank": None if bank is None else money_str(bank),
                "ledger": None if booked is None else money_str(booked),
                "journal": None if cash is None else cash.number,
                "agrees": bank is not None and booked is not None and line.amount == bank == booked,
            }
        )
    return {"request": request.id, "status": request.status, "currency": request.currency, "lines": rows}


def as_dict(session: Session, request: FundingRequest) -> dict:
    return {
        "id": request.id,
        "period": request.period,
        "entity_code": request.entity_code,
        "currency": request.currency,
        "requested_by": request.requested_by,
        "requested_on": request.requested_on,
        "reason": request.reason,
        "status": request.status,
        "total": money_str(sum((row.amount for row in lines_of(session, request) if row.status != "voided"), ZERO)),
        "lines": [
            {
                "payment_id": row.payment_id,
                "idempotency_key": row.idempotency_key,
                "employee_number": row.employee_number,
                "legal_name": row.legal_name,
                "amount": money_str(row.amount),
                "destination_account": row.destination_account,
                "status": row.status,
                "sends": row.sends,
            }
            for row in lines_of(session, request)
        ],
    }

