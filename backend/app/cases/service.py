"""Cases: the work item. Every problem a detector finds becomes one case on one subject.

The workflow, enforced here:

  open -> investigating -> waiting_on_human -> (answered | partially_answered | declined | expired)
       -> ready | held | rejected_duplicate | needs_human -> posted | closed

Code, not the model, decides each case's outcome from what is recorded: the latest finding,
the answers people gave, and the state of the records linked to the case. Every step is an
append-only transition with an evidence row, and nothing lives only in memory, so after a
restart `resume` picks up every case where it stood.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.identities import CONTROLLER_HANDLE, EQUITY_SEAT, IDENTITIES, PAYROLL_SEAT, Actor
from app.auth.permissions import PRECEDENT_RULE, allowed, require
from app.cases.models import Case, CaseAnswer, CaseLink, CaseQuestion, CaseTransition
from app.controls.clock import add_days, days_between, today
from app.controls.gate import check_role, deny, enforce
from app.ledger.errors import LedgerError
from app.ledger.models import JournalEntry
from app.policy.loader import load_policy
from app.reconcile.models import Reconciliation
from app.record.evidence import record

TRANSITIONS: dict[str, set[str]] = {
    "open": {"investigating"},
    "investigating": {"waiting_on_human", "ready", "held", "rejected_duplicate", "needs_human", "closed"},
    "waiting_on_human": {"answered", "partially_answered", "declined", "expired", "posted", "closed"},
    "answered": {"investigating"},
    "partially_answered": {"waiting_on_human"},
    "declined": {"held"},
    "expired": {"waiting_on_human", "needs_human"},
    "ready": {"waiting_on_human", "declined", "posted", "closed"},
    "held": {"investigating", "waiting_on_human", "declined", "closed"},
    "rejected_duplicate": {"waiting_on_human", "closed"},
    "needs_human": {"investigating", "closed"},
    "posted": {"closed"},
    "closed": set(),
}
FINAL = {"posted", "closed"}
ANSWERS = {"confirmed": "answered", "partial": "partially_answered", "declined": "declined"}

EQUITY_KINDS = {
    "forfeiture_still_expensed",
    "missing_grant_approval",
    "vest_schedule_mismatch",
    "duplicate_grant",
    "unknown_platform_grant",
}
DUPLICATE_KINDS = {"duplicate_grant"}
# Money that stays where it is until a person answers, even when other drafts for the month go ahead.
HELD_UNTIL_ANSWERED = {"transfer_not_reflected", "funding_booked_not_cleared"}
ROLE_OF = {handle: role for handle, _name, role, _dept in IDENTITIES}


def seat_for(kind: str) -> str:
    return EQUITY_SEAT if kind in EQUITY_KINDS else PAYROLL_SEAT


def outcome_for(kind: str, draft_allowed: bool | None) -> str:
    """What a finding means for the money, before anyone answers."""
    if kind in DUPLICATE_KINDS:
        return "rejected_duplicate"
    if not draft_allowed or kind in HELD_UNTIL_ANSWERED:
        return "held"
    return "ready"


# ---------------------------------------------------------------- moving a case


def move(session: Session, case: Case, to_state: str, actor: str, reason: str, outcome: str | None = None) -> Case:
    """One step of the workflow. A step the table does not allow is a bug, so it is refused."""
    if to_state not in TRANSITIONS[case.state]:
        raise LedgerError("invalid_transition", f"A case cannot go from {case.state} to {to_state}.", 409)
    _step(session, case, case.state, to_state, actor, reason, outcome)
    return case


def _step(session: Session, case: Case, from_state: str | None, to_state: str, actor: str, reason: str, outcome: str | None) -> None:
    if outcome is not None:
        case.outcome = outcome
    case.state = to_state
    on = today(session)
    event = record(
        session,
        actor,
        "case_opened" if from_state is None else "case_moved",
        "case",
        case.id,
        case_id=case.id,
        reason_code=case.reason_code,
        payload={"key": case.key, "from": from_state, "to": to_state, "outcome": case.outcome, "reason": reason},
        sim_at=on,
    )
    seq = session.scalar(select(func.count()).select_from(CaseTransition).where(CaseTransition.case_id == case.id))
    session.add(
        CaseTransition(
            case_id=case.id,
            seq=int(seq or 0) + 1,
            from_state=from_state,
            to_state=to_state,
            outcome=case.outcome,
            actor=actor,
            reason=reason[:500],
            on=on,
            at=datetime.now(timezone.utc),
            evidence_id=event.id,
        )
    )
    session.flush()


# ---------------------------------------------------------------- opening cases from a reconciliation run


def sync_cases(session: Session, run_id: str, period: str) -> list[Case]:
    """Open a case for every new conflict, refresh the ones already open, and close those whose finding is gone."""
    conflicts = list(
        session.scalars(
            select(Reconciliation)
            .where(Reconciliation.run_id == run_id, Reconciliation.disposition == "conflict")
            .order_by(Reconciliation.kind, Reconciliation.subject_number)
        )
    )
    seen = set()
    cases = []
    for finding in conflicts:
        key = case_key(finding)
        seen.add(key)
        case = session.scalar(select(Case).where(Case.key == key))
        if case is None:
            case = _open(session, finding)
        elif case.state not in FINAL:
            case.finding_id = finding.id
            session.flush()
            record(
                session,
                case.seat,
                "case_reinvestigated",
                "case",
                case.id,
                case_id=case.id,
                payload={"finding": finding.id, "run": run_id},
                sim_at=today(session),
            )
        cases.append(case)
    for case in session.scalars(select(Case).where(Case.period == period, Case.state.not_in(FINAL))):
        if case.key not in seen:
            _withdraw_questions(session, case)
            _close(session, case, case.seat, "The finding no longer stands when the sources are checked again.")
    session.commit()
    return cases


def case_key(finding: Reconciliation) -> str:
    return f"{finding.kind}:{finding.subject_number}:{finding.period}"


def _open(session: Session, finding: Reconciliation) -> Case:
    seat = seat_for(finding.kind)
    case = Case(
        key=case_key(finding),
        period=finding.period,
        kind=finding.kind,
        subject_number=finding.subject_number,
        subject_name=finding.subject_name,
        entity_code=finding.entity_code,
        seat=seat,
        finding_id=finding.id,
        state="open",
        outcome=None,
        reason_code=finding.reason_code,
        opened_on=today(session),
        opened_at=datetime.now(timezone.utc),
    )
    session.add(case)
    session.flush()
    _step(session, case, None, "open", seat, f"Opened from the {finding.kind} finding on {finding.subject_name}.", None)
    move(session, case, "investigating", seat, finding.explanation)
    outcome = outcome_for(finding.kind, finding.draft_allowed)
    owners = json.loads(finding.ask_json)
    if owners:
        for handle in owners:
            ask(session, case, handle, seat, _question_text(finding))
        move(session, case, "waiting_on_human", seat, f"Asked {', '.join(owners)}.", outcome)
    else:
        move(session, case, outcome, seat, "Nobody owns this question in the routing map.", outcome)
    return case


def _question_text(finding: Reconciliation) -> str:
    return f"{finding.subject_name} ({finding.subject_number}): {finding.explanation} Please confirm, or tell us what is wrong."


# ---------------------------------------------------------------- questions and answers


def ask(session: Session, case: Case, handle: str, asked_by: str, text: str, escalates: str | None = None) -> CaseQuestion:
    on = today(session)
    question = CaseQuestion(
        case_id=case.id,
        asked_to=handle,
        asked_by=asked_by,
        question=text,
        asked_on=on,
        due_on=add_days(on, load_policy().reminder_after_days),
        reminders=0,
        status="open",
        escalates=escalates,
        applied=False,
    )
    session.add(question)
    session.flush()
    record(
        session,
        asked_by,
        "question_asked",
        "question",
        question.id,
        case_id=case.id,
        payload={"to": handle, "question": text, "due_on": question.due_on, "escalates": escalates},
        sim_at=on,
    )
    return question


def answer(
    session: Session,
    question_id: str,
    actor: Actor,
    reply: str,
    note: str | None = None,
    precedent: dict | None = None,
) -> Case:
    """Store a person's answer, then let the case act on it. Two steps, so a crash between them loses nothing."""
    question = session.get(CaseQuestion, question_id)
    if question is None:
        raise LedgerError("unknown_question", f"No question {question_id}.", 404)
    enforce(check_role(actor, "answer"), actor, "answer")
    if question.asked_to != actor.handle:
        enforce(deny("ACC-1", f"The question was asked of {question.asked_to}, not {actor.handle}."), actor, "answer")
    if question.status != "open":
        raise LedgerError("question_closed", f"This question is {question.status}.", 409)
    if reply not in ANSWERS:
        raise LedgerError("invalid_answer", "Answer confirmed, partial or declined.", 422)
    note = (note or "").strip() or None
    if reply != "confirmed" and note is None:
        raise LedgerError("invalid_note", "A partial answer or a decline needs a note.", 422)
    if precedent is not None and not allowed(actor, PRECEDENT_RULE):
        require(actor, PRECEDENT_RULE, "rule a precedent")
    on = today(session)
    event = record(
        session,
        actor.handle,
        "question_answered",
        "question",
        question.id,
        case_id=question.case_id,
        payload={"answer": reply, "note": note, "precedent": precedent},
        sim_at=on,
    )
    session.add(
        CaseAnswer(
            question_id=question.id,
            answered_by=actor.handle,
            answer=reply,
            note=note,
            answered_on=on,
            answered_at=datetime.now(timezone.utc),
            evidence_id=event.id,
        )
    )
    question.status = ANSWERS[reply]
    session.commit()
    case = session.get(Case, question.case_id)
    if precedent is not None:
        _rule_precedent(session, [case], actor, precedent, note or "Confirmed by the controller.")
    apply_answers(session, case)
    session.commit()
    return case


def apply_answers(session: Session, case: Case) -> None:
    """Act on every stored answer the case has not acted on yet."""
    for question in _questions(session, case):
        if question.status in ("open", "expired", "withdrawn") or question.applied:
            continue
        latest = _latest_answer(session, question)
        who = latest.answered_by
        if case.state in FINAL:
            question.applied = True
            continue
        if question.status == "declined":
            if case.state != "declined":
                move(session, case, "declined", who, f"{who} declined: {latest.note}")
            move(session, case, "held", case.seat, "Held after the decline; nothing moves until the owner fixes the source.", "held")
        elif question.status == "partially_answered":
            move(session, case, "partially_answered", who, f"{who} answered in part: {latest.note}")
            follow_up = ask(
                session,
                case,
                question.asked_to,
                case.seat,
                f"Thank you. Your answer covered part of it ({latest.note}). What is still open: {question.question}",
            )
            follow_up.applied = False
            move(session, case, "waiting_on_human", case.seat, f"Asked {question.asked_to} a follow-up.")
        else:
            _reinvestigate(session, case, who)
        question.applied = True
        session.flush()


def _reinvestigate(session: Session, case: Case, who: str) -> None:
    """An answer came back: check the sources again and let code decide what the case is now."""
    from app.reconcile.engine import reconcile_period

    if case.state == "waiting_on_human":
        move(session, case, "answered", who, f"{who} confirmed.")
    if case.state != "investigating":
        move(session, case, "investigating", case.seat, "Checking the sources again after the answer.")
    run, _ = reconcile_period(session, case.period, Actor(case.seat, "agent"))
    finding = session.scalar(
        select(Reconciliation).where(
            Reconciliation.run_id == run.id,
            Reconciliation.kind == case.kind,
            Reconciliation.subject_number == case.subject_number,
        )
    )
    if finding is None or finding.disposition != "conflict":
        _withdraw_questions(session, case)
        why = finding.explanation if finding is not None else "The finding no longer appears."
        _close(session, case, case.seat, f"Resolved by the answer and the sources: {why}")
        return
    case.finding_id = finding.id
    outcome = outcome_for(finding.kind, finding.draft_allowed)
    still_open = [row.asked_to for row in _questions(session, case) if row.status == "open"]
    if still_open:
        move(session, case, "waiting_on_human", case.seat, f"Still waiting on {', '.join(still_open)}.", outcome)
    else:
        move(session, case, outcome, case.seat, f"Every question answered; the finding still stands: {finding.explanation}", outcome)


def _rule_precedent(session: Session, cases: list[Case], actor: Actor, precedent: dict, note: str) -> None:
    from app.record.precedents import rule_precedent

    row = rule_precedent(
        session,
        actor,
        rule=str(precedent.get("rule", "")),
        scope=dict(precedent.get("scope") or {}),
        ruling=str(precedent.get("ruling") or note),
        reason=note,
        valid_from=today(session),
    )
    for case in cases:
        record(
            session,
            actor.handle,
            "precedent_from_case",
            "case",
            case.id,
            case_id=case.id,
            payload={"precedent": row.id},
            basis=[{"type": "precedent", "id": row.id, "decided_by": row.decided_by, "on": row.decided_on}],
            sim_at=today(session),
        )


def _questions(session: Session, case: Case) -> list[CaseQuestion]:
    return list(session.scalars(select(CaseQuestion).where(CaseQuestion.case_id == case.id).order_by(CaseQuestion.asked_on, CaseQuestion.id)))


def _latest_answer(session: Session, question: CaseQuestion) -> CaseAnswer:
    return session.scalar(
        select(CaseAnswer).where(CaseAnswer.question_id == question.id).order_by(CaseAnswer.answered_at.desc()).limit(1)
    )


def _withdraw_questions(session: Session, case: Case) -> None:
    for question in _questions(session, case):
        if question.status == "open":
            question.status = "withdrawn"
            question.applied = True
    session.flush()


def _close(session: Session, case: Case, actor: str, reason: str) -> None:
    if case.state == "closed":
        return
    if "closed" not in TRANSITIONS[case.state]:
        move(session, case, "investigating", actor, "Re-checking before closing.")
    move(session, case, "closed", actor, reason)


# ---------------------------------------------------------------- what other records tell a case


def link(session: Session, case: Case, resource: str, resource_id: str) -> None:
    exists = session.scalar(
        select(CaseLink).where(CaseLink.case_id == case.id, CaseLink.resource == resource, CaseLink.resource_id == resource_id)
    )
    if exists is None:
        session.add(CaseLink(case_id=case.id, resource=resource, resource_id=resource_id))
        session.flush()


def cases_linked_to(session: Session, resource: str, resource_id: str) -> list[Case]:
    ids = session.scalars(select(CaseLink.case_id).where(CaseLink.resource == resource, CaseLink.resource_id == resource_id))
    return [session.get(Case, case_id) for case_id in ids]


def on_declined(session: Session, resource: str, resource_id: str, actor: Actor, note: str, precedent: dict | None) -> None:
    """An approver said no to something a case asked for: the case is held with the reason, and may set a precedent."""
    cases = [case for case in cases_linked_to(session, resource, resource_id) if case.state not in FINAL]
    for case in cases:
        move(session, case, "declined", actor.handle, f"{actor.handle} declined the {resource.replace('_', ' ')}: {note}")
        move(session, case, "held", case.seat, "Held after the decline; the plan changes until the reason is dealt with.", "held")
    if precedent is not None and cases:
        # One ruling, cited by every case the decline touched.
        _rule_precedent(session, cases, actor, precedent, note)
    session.commit()


def refresh_posted(session: Session, case: Case) -> bool:
    """A ready case is posted once every live journal it drafted is posted and every payment it asked for is confirmed."""
    from app.controls.models import FundingRequest

    if case.state in FINAL or case.outcome != "ready":
        return False
    links = list(session.scalars(select(CaseLink).where(CaseLink.case_id == case.id)))
    entries = [session.get(JournalEntry, row.resource_id) for row in links if row.resource == "journal_entry"]
    entries = [row for row in entries if row is not None and row.status in ("draft", "approved", "posted")]
    funding = [session.get(FundingRequest, row.resource_id) for row in links if row.resource == "funding_request"]
    funding = [row for row in funding if row is not None and row.status not in ("declined", "voided")]
    if not entries and not funding:
        return False
    if all(row.status == "posted" for row in entries) and all(row.status == "confirmed" for row in funding):
        move(session, case, "posted", "posting.service", "Every entry the case drafted is posted and every payment it asked for is confirmed.")
        return True
    return False


def on_entry_posted(session: Session, entry_id: str) -> None:
    for case in cases_linked_to(session, "journal_entry", entry_id):
        refresh_posted(session, case)
    session.commit()


def link_drafts(session: Session, period: str) -> None:
    """Tie each ready case to the live journals its own seat built from its subject's rows."""
    from app.ledger.models import JournalLine, Period
    from app.ledger.service import parse_period

    year, month = parse_period(period)
    ready: dict[tuple[str, str], Case] = {
        (case.seat, case.subject_number): case
        for case in session.scalars(select(Case).where(Case.period == period, Case.outcome == "ready", Case.state.not_in(FINAL)))
    }
    if not ready:
        return
    entries = session.scalars(
        select(JournalEntry)
        .join(Period, Period.id == JournalEntry.period_id)
        .where(Period.year == year, Period.month == month, JournalEntry.status.in_(("draft", "approved", "posted")))
    )
    for entry in entries:
        subjects: set[str] = set()
        for line in session.scalars(select(JournalLine).where(JournalLine.entry_id == entry.id)):
            for source in json.loads(line.sources_json or "[]"):
                subjects |= _subjects_of(session, source)
        for subject in subjects:
            case = ready.get((entry.prepared_by, subject))
            if case is not None:
                link(session, case, "journal_entry", entry.id)
    session.commit()


def _subjects_of(session: Session, source: dict) -> set[str]:
    from app.equity.models import EquityGrantMonth
    from app.world.models import FundingInstruction, HrCensusRow, PayrollRegisterRow

    model = {
        "payroll_register_rows": PayrollRegisterRow,
        "hr_census_rows": HrCensusRow,
        "equity_grant_months": EquityGrantMonth,
        "funding_instructions": FundingInstruction,
    }.get(source.get("table"))
    row = session.get(model, source.get("id")) if model is not None else None
    if row is None:
        return set()
    subjects = {row.employee_number}
    if isinstance(row, FundingInstruction):
        subjects.add(row.payment_id)
    return subjects


# ---------------------------------------------------------------- the clock and restarts


def tick(session: Session) -> list[dict]:
    """Remind, then escalate, every unanswered question. Past the last role, the case needs a person."""
    policy = load_policy()
    on = today(session)
    changes = []
    for question in list(session.scalars(select(CaseQuestion).where(CaseQuestion.status == "open"))):
        waited = days_between(question.asked_on, on)
        due = waited // policy.reminder_after_days
        if due > question.reminders:
            question.reminders = due
            question.due_on = add_days(question.asked_on, policy.reminder_after_days * (due + 1))
            session.flush()
            record(
                session,
                "vestly.system",
                "question_reminder",
                "question",
                question.id,
                case_id=question.case_id,
                payload={"to": question.asked_to, "reminder": due, "waited_days": waited},
                sim_at=on,
            )
            changes.append({"question": question.id, "change": "reminder", "count": due})
        if question.reminders >= policy.escalate_after_reminders:
            changes.append(_expire(session, question, waited))
    return changes


def _expire(session: Session, question: CaseQuestion, waited: int) -> dict:
    case = session.get(Case, question.case_id)
    question.status = "expired"
    question.applied = True
    session.flush()
    if case.state != "waiting_on_human":
        # Already in a person's hands, held, or done: the lapse is recorded, the case does not move.
        record(
            session,
            "vestly.system",
            "question_expired",
            "question",
            question.id,
            case_id=case.id,
            payload={"to": question.asked_to, "waited_days": waited, "case_state": case.state},
            sim_at=today(session),
        )
        return {"question": question.id, "change": "expired"}
    move(session, case, "expired", "vestly.system", f"{question.asked_to} did not answer in {waited} days.")
    target_role = load_policy().escalates_to(ROLE_OF.get(question.asked_to, ""))
    target = CONTROLLER_HANDLE if target_role == "controller" else None
    if target is None or target == question.asked_to:
        move(session, case, "needs_human", "vestly.system", "Nobody further up can answer; a person must take the case.", "needs_human")
        return {"question": question.id, "change": "needs_human", "case": case.id}
    ask(
        session,
        case,
        target,
        "vestly.system",
        f"Escalated: {question.asked_to} has not answered for {waited} days. {question.question}",
        escalates=question.id,
    )
    move(session, case, "waiting_on_human", "vestly.system", f"Escalated to {target}.")
    return {"question": question.id, "change": "escalated", "to": target}


def resume(session: Session) -> dict:
    """After a restart: act on answers stored but not yet applied, finish released payments, and post what is done."""
    from app.controls.posting import resume_funding

    applied = 0
    for case in list(session.scalars(select(Case).where(Case.state.not_in(FINAL)))):
        pending = [row for row in _questions(session, case) if row.status not in ("open", "expired", "withdrawn") and not row.applied]
        if pending:
            apply_answers(session, case)
            applied += len(pending)
    session.commit()
    funding = resume_funding(session)
    for case in list(session.scalars(select(Case).where(Case.state.not_in(FINAL)))):
        refresh_posted(session, case)
    session.commit()
    return {"answers_applied": applied, **funding}


# ---------------------------------------------------------------- reading


def timeline(session: Session, case: Case) -> dict:
    finding = session.get(Reconciliation, case.finding_id)
    steps = session.scalars(select(CaseTransition).where(CaseTransition.case_id == case.id).order_by(CaseTransition.seq))
    questions = []
    for question in _questions(session, case):
        answers = session.scalars(select(CaseAnswer).where(CaseAnswer.question_id == question.id).order_by(CaseAnswer.answered_at))
        questions.append(
            {
                "id": question.id,
                "asked_to": question.asked_to,
                "asked_by": question.asked_by,
                "question": question.question,
                "asked_on": question.asked_on,
                "due_on": question.due_on,
                "status": question.status,
                "reminders": question.reminders,
                "escalates": question.escalates,
                "answers": [
                    {"by": row.answered_by, "answer": row.answer, "note": row.note, "on": row.answered_on, "evidence": row.evidence_id}
                    for row in answers
                ],
            }
        )
    links = session.scalars(select(CaseLink).where(CaseLink.case_id == case.id))
    return {
        **as_dict(case),
        "finding": {
            "id": finding.id,
            "explanation": finding.explanation,
            "trusted_value": finding.trusted_value,
            "authority_row": finding.fact,
            "ranking": json.loads(finding.ranking_json),
            "basis": json.loads(finding.basis_json),
            "draft_allowed": finding.draft_allowed,
        },
        "steps": [
            {"seq": row.seq, "from": row.from_state, "to": row.to_state, "outcome": row.outcome, "by": row.actor, "reason": row.reason, "on": row.on, "evidence": row.evidence_id}
            for row in steps
        ],
        "questions": questions,
        "links": [{"resource": row.resource, "id": row.resource_id} for row in links],
    }


def as_dict(case: Case) -> dict:
    return {
        "id": case.id,
        "key": case.key,
        "period": case.period,
        "kind": case.kind,
        "subject_number": case.subject_number,
        "subject_name": case.subject_name,
        "entity_code": case.entity_code,
        "seat": case.seat,
        "state": case.state,
        "outcome": case.outcome,
        "reason_code": case.reason_code,
        "opened_on": case.opened_on,
    }
