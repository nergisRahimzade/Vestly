"""Read the record: check the chain, list events, walk a journal line back to its sources, find precedents."""

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import AUDIT_READ, PRECEDENT_READ, SOURCES_INDIVIDUAL, require
from app.db import get_db
from app.equity.models import EquityGrantMonth
from app.ledger.errors import LedgerError
from app.ledger.models import JournalEntry, JournalLine
from app.ledger.money import money_str
from app.ledger.service import period_label
from app.record.evidence import as_dict as event_dict
from app.record.evidence import verify
from app.record.models import EvidenceEvent, Precedent
from app.record.precedents import as_dict as precedent_dict
from app.record.precedents import rule_precedent, search_precedents
from app.world.models import FundingInstruction, HrCensusRow, PayrollRegisterRow, SourceDocument

router = APIRouter()

SOURCE_TABLES = {
    "payroll_register_rows": PayrollRegisterRow,
    "hr_census_rows": HrCensusRow,
    "funding_instructions": FundingInstruction,
    "equity_grant_months": EquityGrantMonth,
    "journal_lines": JournalLine,
}


def audit_reader(who: Actor = Depends(current_actor)) -> Actor:
    require(who, AUDIT_READ, "read the evidence log")
    return who


def precedent_reader(who: Actor = Depends(current_actor)) -> Actor:
    require(who, PRECEDENT_READ, "read precedents")
    return who


@router.get("/audit/verify")
def verify_chain(db: Session = Depends(get_db), _who: Actor = Depends(audit_reader)) -> dict:
    check = verify(db)
    return {"ok": check.ok, "events": check.events, "first_broken": check.first_broken, "reason": check.reason}


@router.get("/audit/events")
def list_events(
    subject_type: str | None = None,
    subject_id: str | None = None,
    action: str | None = None,
    case_id: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _who: Actor = Depends(audit_reader),
) -> list[dict]:
    query = select(EvidenceEvent).order_by(EvidenceEvent.seq)
    for column, value in (
        (EvidenceEvent.subject_type, subject_type),
        (EvidenceEvent.subject_id, subject_id),
        (EvidenceEvent.action, action),
        (EvidenceEvent.case_id, case_id),
    ):
        if value is not None:
            query = query.where(column == value)
    return [event_dict(row) for row in db.scalars(query.limit(max(1, min(limit, 1000))))]


@router.get("/audit/trace/journal-line/{line_id}")
def trace_journal_line(line_id: str, db: Session = Depends(get_db), who: Actor = Depends(audit_reader)) -> dict:
    """From one journal line back to the rows, documents, policy and people behind it.

    Source rows hold one person's pay, so tracing needs individual access.
    """
    require(who, SOURCES_INDIVIDUAL, "trace a journal line to individual source rows")
    line = db.scalar(
        select(JournalLine)
        .where(JournalLine.id == line_id)
        .options(selectinload(JournalLine.account), selectinload(JournalLine.entry))
    )
    if line is None:
        raise LedgerError("unknown_line", f"Journal line {line_id} does not exist.", 404)
    entry: JournalEntry = line.entry
    provenance = json.loads(entry.provenance_json) if entry.provenance_json else {}
    sources = [_resolve(db, source) for source in json.loads(line.sources_json or "[]")]
    hashes = set(provenance.get("documents", [])) | {
        source["document"] for source in sources if source.get("document")
    }
    documents = [
        {"sha256": row.content_hash, "title": row.title, "system": row.system, "period": row.period}
        for row in db.scalars(select(SourceDocument).where(SourceDocument.content_hash.in_(hashes)))
    ]
    events = db.scalars(
        select(EvidenceEvent)
        .where(EvidenceEvent.subject_type == "journal_entry", EvidenceEvent.subject_id == entry.id)
        .order_by(EvidenceEvent.seq)
    )
    return {
        "line": {
            "id": line.id,
            "line_number": line.line_number,
            "account_code": line.account.code,
            "account_name": line.account.name,
            "department": line.department,
            "debit": money_str(line.debit),
            "credit": money_str(line.credit),
        },
        "entry": {
            "id": entry.id,
            "number": entry.number,
            "entity_code": entry.entity.code,
            "period": period_label(entry.period),
            "memo": entry.memo,
            "status": entry.status,
            "booking_key": entry.booking_key,
        },
        "people": {
            "prepared_by": entry.prepared_by,
            "reviewed_by": entry.reviewed_by,
            "posted_by": entry.posted_by,
        },
        "policy": {"version": provenance.get("policy_version"), "hash": provenance.get("policy_hash")},
        "provenance": provenance,
        "sources": sources,
        "documents": sorted(documents, key=lambda row: (row["period"], row["title"])),
        "events": [event_dict(row) for row in events],
    }


def _resolve(db: Session, source: dict) -> dict:
    """The stored row a source pointer names, as it is now. The pointer's document hash shows what it was."""
    model = SOURCE_TABLES.get(source.get("table"))
    row = db.get(model, source.get("id")) if model is not None else None
    resolved = dict(source)
    if row is None:
        resolved["found"] = False
        return resolved
    resolved["found"] = True
    if isinstance(row, (PayrollRegisterRow, HrCensusRow, FundingInstruction)):
        resolved["employee_number"] = row.employee_number
        resolved["legal_name"] = row.legal_name
        resolved["period"] = row.period
    if isinstance(row, PayrollRegisterRow):
        resolved["gross_wages"] = money_str(row.gross_wages)
        resolved["employer_contribution"] = money_str(row.employer_contribution)
    if isinstance(row, HrCensusRow):
        resolved["status"] = row.status
        resolved["termination_date"] = row.termination_date
    if isinstance(row, FundingInstruction):
        resolved["payment_id"] = row.payment_id
        resolved["amount"] = money_str(row.amount)
    if isinstance(row, EquityGrantMonth):
        resolved.update(
            legal_name=row.legal_name,
            status=row.status,
            booked_net_expense=None if row.booked_net_expense is None else money_str(row.booked_net_expense),
        )
    if isinstance(row, JournalLine):
        resolved.update(debit=money_str(row.debit), credit=money_str(row.credit), department=row.department)
    return resolved


class PrecedentIn(BaseModel):
    rule: str = Field(min_length=1, max_length=16)
    scope: dict[str, str] = Field(default_factory=dict)
    ruling: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    valid_from: str
    expires: str | None = None
    overrules: str | None = None
    source_document_hash: str | None = None


@router.post("/precedents", status_code=201)
def create_precedent(body: PrecedentIn, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    """The controller rules. Overruling an earlier precedent is a new one that names it."""
    return precedent_dict(rule_precedent(db, who, **body.model_dump()))


@router.get("/precedents")
def list_precedents(db: Session = Depends(get_db), _who: Actor = Depends(precedent_reader)) -> list[dict]:
    """Every precedent ever ruled, overruled ones included: nothing is deleted."""
    return [precedent_dict(row) for row in db.scalars(select(Precedent).order_by(Precedent.valid_from, Precedent.id))]


@router.get("/precedents/search")
def find_precedents(
    on: str,
    rule: str | None = None,
    employee: str | None = None,
    entity: str | None = None,
    fact: str | None = None,
    account: str | None = None,
    db: Session = Depends(get_db),
    _who: Actor = Depends(precedent_reader),
) -> list[dict]:
    rows = search_precedents(db, on, rule=rule, employee=employee, entity=entity, fact=fact, account=account)
    return [precedent_dict(row) for row in rows]


@router.get("/precedents/{precedent_id}")
def get_precedent(precedent_id: str, db: Session = Depends(get_db), _who: Actor = Depends(precedent_reader)) -> dict:
    row = db.get(Precedent, precedent_id)
    if row is None:
        raise LedgerError("unknown_precedent", f"No precedent {precedent_id}.", 404)
    return precedent_dict(row)


@router.get("/audit/errors")
def list_errors(limit: int = 100, db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> list[dict]:
    """Unexpected errors, newest first: where, why, and what was being done."""
    import json as _json

    from app.record.models import ErrorLog

    require(who, AUDIT_READ, "read the error log")
    rows = db.scalars(select(ErrorLog).order_by(ErrorLog.at.desc()).limit(max(1, min(limit, 1000))))
    return [
        {
            "id": row.id,
            "at": row.at,
            "where": row.where,
            "error_type": row.error_type,
            "message": row.message,
            "steps": _json.loads(row.steps_json),
            "request": row.request,
            "actor": row.actor,
            "traceback": row.traceback,
        }
        for row in rows
    ]


@router.get("/audit/replay")
def replay_record(
    on: str,
    employee: str | None = None,
    case_id: str | None = None,
    db: Session = Depends(get_db),
    who: Actor = Depends(current_actor),
) -> dict:
    """What Vestly knew on a date: drops received by then, rulings in force, decisions recorded, the policy version."""
    from app.record.replay import replay

    require(who, AUDIT_READ, "replay the record")
    return replay(db, on, employee=employee, case_id=case_id)
