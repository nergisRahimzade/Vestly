"""The approvals inbox. Vestly asks; a person decides; the gate checks who may say yes.

A request is bound to the exact entry it was made for. If the entry changes before the
last approval, the request is voided and a fresh one goes out (BND-1). Above the approval
limit two people must approve, one of them the controller (LIM-1). Nobody approves what
they asked for or prepared (SOD-1), or a payroll run that pays a change they keyed (SOD-2).
"""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.identities import Actor
from app.controls.clock import add_days, days_between, today
from app.controls.gate import check_role, comp_changes_entered_by, deny, enforce
from app.controls.models import ApprovalDecision, ApprovalRequest
from app.ledger.errors import LedgerError
from app.ledger.models import JournalEntry, JournalLine
from app.ledger.money import CENT, money_str
from app.ledger.service import period_label, review_entry
from app.policy.loader import load_policy
from app.policy.materiality import rate_to
from app.record.evidence import canonical, record
from app.world.loader import rate_card_for
from app.world.models import PayrollRegisterRow

KIND_JOURNAL = "journal_entry"
KIND_FUNDING = "funding_request"


# ---------------------------------------------------------------- binding


def journal_binding(entry: JournalEntry) -> dict:
    """Everything an approver is agreeing to. Changing any of it voids the approval."""
    provenance = json.loads(entry.provenance_json) if entry.provenance_json else {}
    return {
        "kind": KIND_JOURNAL,
        "entry_id": entry.id,
        "number": entry.number,
        "entity": entry.entity.code,
        "period": period_label(entry.period),
        "booking_key": entry.booking_key,
        "case_id": provenance.get("case_id"),
        "currency": entry.currency,
        "lines": [
            [line.account.code, line.department, money_str(line.debit), money_str(line.credit)]
            for line in sorted(entry.lines, key=lambda item: item.line_number)
        ],
    }


def binding_hash(binding: dict) -> str:
    return hashlib.sha256(canonical(binding).encode("utf-8")).hexdigest()


def _total(entry: JournalEntry) -> Decimal:
    return sum((line.debit for line in entry.lines), Decimal("0.00"))


def _usd(session: Session, amount: Decimal, currency: str, period: str) -> Decimal | None:
    rate = rate_to(rate_card_for(session, period) or {}, currency)
    return None if rate is None else (amount * rate).quantize(CENT)


# ---------------------------------------------------------------- requests


def open_request(session: Session, entry: JournalEntry, requested_by: str) -> ApprovalRequest:
    """Put an entry in front of approvers. Called when Vestly drafts, and again after a voided request."""
    policy = load_policy()
    period = period_label(entry.period)
    amount = _total(entry)
    amount_usd = _usd(session, amount, entry.currency, period)
    # Without a rate the size cannot be measured, so the stricter path applies.
    over_limit = amount_usd is None or amount_usd > policy.approval_limit
    binding = journal_binding(entry)
    on = today(session)
    request = ApprovalRequest(
        kind=KIND_JOURNAL,
        subject_id=entry.id,
        case_id=binding["case_id"],
        entity_code=entry.entity.code,
        period=period,
        requested_by=requested_by,
        requested_on=on,
        requested_at=datetime.now(timezone.utc),
        binding_hash=binding_hash(binding),
        binding_json=canonical(binding),
        amount=amount,
        currency=entry.currency,
        amount_usd=amount_usd,
        approvals_needed=2 if over_limit else 1,
        needs_controller=over_limit,
        inbox_roles=json.dumps(["controller", "finance_mgr"] if over_limit else ["finance_mgr"]),
        status="pending",
        due_on=add_days(on, policy.reminder_after_days),
        reminders=0,
        escalated=False,
    )
    session.add(request)
    session.flush()
    record(
        session,
        requested_by,
        "approval_requested",
        "approval_request",
        request.id,
        case_id=request.case_id,
        payload={
            "journal": entry.number,
            "amount": money_str(amount),
            "currency": entry.currency,
            "amount_usd": None if amount_usd is None else money_str(amount_usd),
            "approvals_needed": request.approvals_needed,
            "needs_controller": over_limit,
            "binding_hash": request.binding_hash,
            "due_on": request.due_on,
        },
        basis=[{"type": "rule", "id": "LIM-1"}] if over_limit else [],
        sim_at=on,
    )
    return request


def open_funding_request(session: Session, funding, requested_by: str, case_ids: list[str]) -> ApprovalRequest:
    """Put a funding batch in front of treasury. Above the limit the controller must approve too (LIM-1)."""
    from app.controls.posting import funding_binding, lines_of

    policy = load_policy()
    amount = sum((row.amount for row in lines_of(session, funding) if row.status != "voided"), Decimal("0.00"))
    amount_usd = _usd(session, amount, funding.currency, funding.period)
    over_limit = amount_usd is None or amount_usd > policy.approval_limit
    binding = funding_binding(session, funding, case_ids)
    on = today(session)
    request = ApprovalRequest(
        kind=KIND_FUNDING,
        subject_id=funding.id,
        case_id=case_ids[0] if case_ids else None,
        entity_code=funding.entity_code,
        period=funding.period,
        requested_by=requested_by,
        requested_on=on,
        requested_at=datetime.now(timezone.utc),
        binding_hash=binding_hash(binding),
        binding_json=canonical(binding),
        amount=amount,
        currency=funding.currency,
        amount_usd=amount_usd,
        approvals_needed=2 if over_limit else 1,
        needs_controller=over_limit,
        inbox_roles=json.dumps(["treasury", "controller"] if over_limit else ["treasury"]),
        status="pending",
        due_on=add_days(on, policy.reminder_after_days),
        reminders=0,
        escalated=False,
    )
    session.add(request)
    session.flush()
    record(
        session,
        requested_by,
        "approval_requested",
        "approval_request",
        request.id,
        case_id=request.case_id,
        payload={
            "funding_request": funding.id,
            "amount": money_str(amount),
            "currency": funding.currency,
            "amount_usd": None if amount_usd is None else money_str(amount_usd),
            "approvals_needed": request.approvals_needed,
            "needs_controller": over_limit,
            "binding_hash": request.binding_hash,
            "due_on": request.due_on,
        },
        basis=[{"type": "rule", "id": "LIM-1"}] if over_limit else [],
        sim_at=on,
    )
    return request


def pending_request(session: Session, entry_id: str) -> ApprovalRequest | None:
    return session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.kind == KIND_JOURNAL,
            ApprovalRequest.subject_id == entry_id,
            ApprovalRequest.status == "pending",
        )
    )


def close_for_withdrawn_entry(session: Session, entry: JournalEntry, actor: str) -> None:
    request = pending_request(session, entry.id)
    if request is None:
        return
    request.status = "withdrawn"
    session.flush()
    record(session, actor, "approval_withdrawn", "approval_request", request.id, sim_at=today(session))


def decisions(session: Session, request: ApprovalRequest) -> list[ApprovalDecision]:
    return list(
        session.scalars(
            select(ApprovalDecision)
            .where(ApprovalDecision.request_id == request.id)
            .order_by(ApprovalDecision.decided_at)
        )
    )


def decide_entry(session: Session, entry_id: str, actor: Actor, approve: bool, note: str | None) -> JournalEntry:
    """Approve or decline a journal through its pending request, opening one if the entry has none."""
    entry = _entry(session, entry_id)
    if entry.status != "draft":
        raise LedgerError("journal_is_final", f"{entry.number} is {entry.status}, not a draft.", 409)
    request = pending_request(session, entry.id) or open_request(session, entry, entry.prepared_by)
    decide(session, request.id, actor, approve, note)
    return _entry(session, entry_id)


def decide(
    session: Session, request_id: str, actor: Actor, approve: bool, note: str | None, precedent: dict | None = None
) -> ApprovalRequest:
    request = session.get(ApprovalRequest, request_id)
    if request is None:
        raise LedgerError("unknown_request", f"No approval request {request_id}.", 404)
    if request.status != "pending":
        raise LedgerError("request_closed", f"This request is {request.status}.", 409)
    if precedent is not None:
        # Only the controller turns a decline into a precedent; checked before anything is recorded.
        enforce(check_role(actor, "rule_precedent"), actor, "rule_precedent")
        if approve:
            raise LedgerError("invalid_precedent", "A precedent comes from a decline, not an approval.", 422)
    if request.kind == KIND_FUNDING:
        return _decide_funding(session, request, actor, approve, note, precedent)
    entry = _entry(session, request.subject_id)
    action = "approve_entry"
    period = request.period

    # SOD-1 first: Vestly trying to approve its own request should hear "separation of duties", not just "no role".
    if actor.handle in (request.requested_by, entry.prepared_by):
        enforce(deny("SOD-1", f"{actor.handle} asked for or prepared {entry.number}."), actor, action)
    paid = _employees_paid(session, entry)
    keyed = comp_changes_entered_by(session, paid, period)
    if actor.handle in keyed:
        people = ", ".join(sorted(keyed[actor.handle]))
        enforce(deny("SOD-2", f"{actor.handle} keyed a pay change for {people} that {entry.number} pays."), actor, action)
    enforce(check_role(actor, action), actor, action)
    earlier = decisions(session, request)
    if any(row.decided_by == actor.handle for row in earlier):
        raise LedgerError("already_decided", f"{actor.handle} has already answered this request.", 409)

    current = journal_binding(entry)
    if binding_hash(current) != request.binding_hash:
        _void_and_reissue(session, request, entry, actor)
        session.commit()
        enforce(
            deny("BND-1", f"{entry.number} changed after it was put up for approval. A new request has been opened."),
            actor,
            action,
        )

    on = today(session)
    if not approve:
        if not (note or "").strip():
            raise LedgerError("invalid_note", "A decline needs a reason.", 422)
        _add_decision(session, request, actor, "declined", note, on)
        request.status = "declined"
        session.flush()
        record(
            session,
            actor.handle,
            "approval_declined",
            "approval_request",
            request.id,
            case_id=request.case_id,
            payload={"journal": entry.number, "note": note},
            sim_at=on,
        )
        review_entry(session, entry.id, actor, approve=False, note=note)
        from app.cases.service import on_declined

        on_declined(session, KIND_JOURNAL, entry.id, actor, note, precedent)
        return request

    approvers = [row for row in earlier if row.decision == "approved"]
    completes = len(approvers) + 1 >= request.approvals_needed
    has_controller = actor.role == "controller" or any(row.role == "controller" for row in approvers)
    if completes and request.needs_controller and not has_controller:
        enforce(
            deny("LIM-1", f"{entry.number} is above the approval limit; the controller must be one of the two approvers."),
            actor,
            action,
        )
    _add_decision(session, request, actor, "approved", note, on)
    session.flush()
    record(
        session,
        actor.handle,
        "approval_given",
        "approval_request",
        request.id,
        case_id=request.case_id,
        payload={
            "journal": entry.number,
            "approval": len(approvers) + 1,
            "of": request.approvals_needed,
            "binding_hash": request.binding_hash,
        },
        basis=[{"type": "rule", "id": "LIM-1"}] if request.needs_controller else [],
        sim_at=on,
    )
    if completes:
        request.status = "approved"
        session.flush()
        review_entry(session, entry.id, actor, approve=True, note=note)
    else:
        session.commit()
    return request


RELEASE_ACTIONS = ("release_funding", "approve_funding")


def _decide_funding(
    session: Session, request: ApprovalRequest, actor: Actor, approve: bool, note: str | None, precedent: dict | None
) -> ApprovalRequest:
    """Treasury releases a funding batch. Above the limit the controller approves as well (LIM-1)."""
    from app.controls.models import FundingRequest
    from app.controls.posting import decline_funding, funding_binding, release

    funding = session.get(FundingRequest, request.subject_id)
    action = "release_funding"
    if actor.handle == request.requested_by:
        enforce(deny("SOD-1", f"{actor.handle} asked for this funding."), actor, action)
    own = load_policy().role_actions(actor.role)
    if not any(item in own for item in RELEASE_ACTIONS):
        enforce(check_role(actor, action), actor, action)
    earlier = decisions(session, request)
    if any(row.decided_by == actor.handle for row in earlier):
        raise LedgerError("already_decided", f"{actor.handle} has already answered this request.", 409)
    binding = json.loads(request.binding_json)
    if binding_hash(funding_binding(session, funding, binding["cases"])) != request.binding_hash:
        request.status = "voided"
        session.flush()
        record(session, actor.handle, "approval_voided", "approval_request", request.id, reason_code="BND-1", sim_at=today(session))
        session.commit()
        enforce(deny("BND-1", "The funding batch changed after it was put up for approval."), actor, action)

    on = today(session)
    if not approve:
        if not (note or "").strip():
            raise LedgerError("invalid_note", "A decline needs a reason.", 422)
        _add_decision(session, request, actor, "declined", note, on)
        request.status = "declined"
        decline_funding(session, funding)
        record(
            session,
            actor.handle,
            "approval_declined",
            "approval_request",
            request.id,
            case_id=request.case_id,
            payload={"funding_request": funding.id, "note": note},
            sim_at=on,
        )
        session.commit()
        from app.cases.service import on_declined

        on_declined(session, KIND_FUNDING, funding.id, actor, note, precedent)
        return request

    approvers = [row for row in earlier if row.decision == "approved"]
    completes = len(approvers) + 1 >= request.approvals_needed
    roles = {row.role for row in approvers} | {actor.role}
    if completes and request.needs_controller and "controller" not in roles:
        enforce(
            deny("LIM-1", "This funding is above the approval limit; the controller must be one of the two approvers."),
            actor,
            action,
        )
    if completes and not any("release_funding" in load_policy().role_actions(role) for role in roles):
        enforce(deny("ACC-1", "Treasury releases funding; the controller's approval alone sends no money."), actor, action)
    _add_decision(session, request, actor, "approved", note, on)
    session.flush()
    record(
        session,
        actor.handle,
        "approval_given",
        "approval_request",
        request.id,
        case_id=request.case_id,
        payload={
            "funding_request": funding.id,
            "approval": len(approvers) + 1,
            "of": request.approvals_needed,
            "binding_hash": request.binding_hash,
        },
        basis=[{"type": "rule", "id": "LIM-1"}] if request.needs_controller else [],
        sim_at=on,
    )
    if completes:
        request.status = "approved"
        funding.status = "approved"
        session.flush()
        release(session, funding, actor.handle)
    else:
        session.commit()
    return request


def _add_decision(
    session: Session, request: ApprovalRequest, actor: Actor, decision: str, note: str | None, on: str
) -> None:
    session.add(
        ApprovalDecision(
            request_id=request.id,
            decided_by=actor.handle,
            role=actor.role,
            decision=decision,
            note=(note or "").strip() or None,
            binding_hash=request.binding_hash,
            decided_on=on,
            decided_at=datetime.now(timezone.utc),
        )
    )


def _void_and_reissue(session: Session, request: ApprovalRequest, entry: JournalEntry, actor: Actor) -> None:
    replacement = open_request(session, entry, request.requested_by)
    request.status = "voided"
    request.replaced_by = replacement.id
    session.flush()
    record(
        session,
        actor.handle,
        "approval_voided",
        "approval_request",
        request.id,
        case_id=request.case_id,
        reason_code="BND-1",
        payload={"journal": entry.number, "was": request.binding_hash, "now": replacement.binding_hash},
        supersedes=None,
        sim_at=today(session),
    )


def _entry(session: Session, entry_id: str) -> JournalEntry:
    entry = session.scalar(
        select(JournalEntry)
        .where(JournalEntry.id == entry_id)
        .options(selectinload(JournalEntry.lines).selectinload(JournalLine.account))
        .execution_options(populate_existing=True)
    )
    if entry is None:
        raise LedgerError("unknown_journal", f"Journal {entry_id} does not exist.", 404)
    return entry


def _employees_paid(session: Session, entry: JournalEntry) -> set[str]:
    """Everyone whose register row this entry was built from."""
    people: set[str] = set()
    for line in entry.lines:
        for source in json.loads(line.sources_json or "[]"):
            if source.get("table") == "payroll_register_rows":
                row = session.get(PayrollRegisterRow, source.get("id"))
                if row is not None:
                    people.add(row.employee_number)
    return people


# ---------------------------------------------------------------- inbox and the clock


def inbox(session: Session, actor: Actor) -> list[ApprovalRequest]:
    rows = session.scalars(
        select(ApprovalRequest).where(ApprovalRequest.status == "pending").order_by(ApprovalRequest.requested_at)
    )
    visible = []
    for row in rows:
        if actor.role not in json.loads(row.inbox_roles) or actor.handle == row.requested_by:
            continue
        if any(item.decided_by == actor.handle for item in decisions(session, row)):
            continue
        visible.append(row)
    return visible


def tick(session: Session) -> list[dict]:
    """Remind, then escalate, every request still waiting. Runs whenever the clock moves."""
    policy = load_policy()
    on = today(session)
    from app.cases.service import tick as tick_questions

    changes = tick_questions(session)
    for request in session.scalars(select(ApprovalRequest).where(ApprovalRequest.status == "pending")):
        waited = days_between(request.requested_on, on)
        due_reminders = waited // policy.reminder_after_days
        if due_reminders > request.reminders:
            request.reminders = due_reminders
            request.due_on = add_days(request.requested_on, policy.reminder_after_days * (due_reminders + 1))
            session.flush()
            record(
                session,
                "vestly.system",
                "approval_reminder",
                "approval_request",
                request.id,
                case_id=request.case_id,
                payload={"reminder": due_reminders, "waited_days": waited, "roles": json.loads(request.inbox_roles)},
                sim_at=on,
            )
            changes.append({"request": request.id, "change": "reminder", "count": due_reminders})
        if request.reminders >= policy.escalate_after_reminders and not request.escalated:
            roles = json.loads(request.inbox_roles)
            added = [target for role in roles if (target := policy.escalates_to(role)) and target not in roles]
            request.inbox_roles = json.dumps(roles + added)
            request.escalated = True
            session.flush()
            record(
                session,
                "vestly.system",
                "approval_escalated",
                "approval_request",
                request.id,
                case_id=request.case_id,
                payload={"from": roles, "added": added, "waited_days": waited},
                sim_at=on,
            )
            changes.append({"request": request.id, "change": "escalated", "to": added})
    return changes


def as_dict(session: Session, row: ApprovalRequest) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "subject_id": row.subject_id,
        "binding": json.loads(row.binding_json),
        "binding_hash": row.binding_hash,
        "entity_code": row.entity_code,
        "period": row.period,
        "requested_by": row.requested_by,
        "requested_on": row.requested_on,
        "amount": money_str(row.amount),
        "currency": row.currency,
        "amount_usd": None if row.amount_usd is None else money_str(row.amount_usd),
        "approvals_needed": row.approvals_needed,
        "needs_controller": row.needs_controller,
        "inbox_roles": json.loads(row.inbox_roles),
        "status": row.status,
        "due_on": row.due_on,
        "reminders": row.reminders,
        "escalated": row.escalated,
        "replaced_by": row.replaced_by,
        "decisions": [
            {"by": item.decided_by, "role": item.role, "decision": item.decision, "note": item.note, "on": item.decided_on}
            for item in decisions(session, row)
        ],
    }
