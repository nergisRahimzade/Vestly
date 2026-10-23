import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.ledger.service import import_posted_entry, lock_period, open_period
from app.world.drops import fixture_dir
from app.world.evidence import load_static_evidence
from app.world.models import (
    BankLine,
    EquitySnapshot,
    FundingInstruction,
    HrCensusRow,
    LedgerExportLine,
    PayrollRegisterRow,
    Person,
    SourceDocument,
    WorldLoad,
)

WORLD_VERSION = "2026-06"
ENTITIES = ("US", "UK", "DE")
CURRENCY = {"US": "USD", "UK": "GBP", "DE": "EUR"}


def load_world(session: Session, actor: Actor) -> dict:
    """Load the file drops and post the books as they stood. The actor must be allowed to post and lock."""
    if session.get(WorldLoad, WORLD_VERSION) is not None:
        return {"status": "already_loaded", "people": _people_count(session)}
    for period in ("2026-03", "2026-06"):
        drop = json.loads((fixture_dir() / f"{period}.json").read_text(encoding="utf-8"))
        store_drop(session, drop)
    load_static_evidence(session)
    session.commit()
    _derive_people(session)
    session.commit()
    _post_history(session, actor)
    session.add(WorldLoad(version=WORLD_VERSION, loaded_at=datetime.now(timezone.utc)))
    session.commit()
    return {"status": "loaded", "people": _people_count(session)}


def _people_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Person)) or 0)


def _document(system: str, period: str, title: str, body: str, received_on: str) -> SourceDocument:
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return SourceDocument(
        system=system,
        period=period,
        title=title,
        body=body,
        content_hash=digest,
        received_on=received_on,
    )


def store_drop(session: Session, drop: dict) -> None:
    """Store one month's file drop as source rows. It does not post anything."""
    period = drop["period"]
    received_on = drop["received_on"]
    sections = {
        "hr": drop["hr"],
        "payroll": drop["payroll"],
        "funding": drop["funding"],
        "equity": drop["equity"],
        "bank": drop["bank"],
        "ledger": drop["ledger_entries"],
    }
    documents = {
        system: _document(
            system,
            period,
            f"Northlight {system} export {period}",
            json.dumps(payload, indent=2) + "\n",
            received_on,
        )
        for system, payload in sections.items()
    }
    for extra in drop["documents"]:
        documents[extra["title"]] = _document(extra["system"], period, extra["title"], extra["body"], received_on)
    session.add_all(documents.values())
    session.flush()
    hr = documents["hr"]
    payroll = documents["payroll"]
    equity = documents["equity"]
    bank = documents["bank"]
    ledger = documents["ledger"]
    session.add_all(HrCensusRow(document_id=hr.id, period=period, **row) for row in drop["hr"])
    session.add_all(PayrollRegisterRow(document_id=payroll.id, period=period, **row) for row in drop["payroll"])
    session.add_all(FundingInstruction(document_id=payroll.id, period=period, **row) for row in drop["funding"])
    session.add_all(EquitySnapshot(document_id=equity.id, period=period, **row) for row in drop["equity"])
    session.add_all(BankLine(document_id=bank.id, period=period, **row) for row in drop["bank"])
    for entry in drop["ledger_entries"]:
        currency = CURRENCY[entry["entity_code"]]
        session.add_all(
            LedgerExportLine(
                document_id=ledger.id,
                period=period,
                entity_code=entry["entity_code"],
                entry_memo=entry["memo"],
                kind=entry["kind"],
                currency=currency,
                account_code=line["account_code"],
                department=line["department"],
                debit=line["debit"],
                credit=line["credit"],
            )
            for line in entry["lines"]
        )


def _derive_people(session: Session) -> None:
    hr_rows = list(session.scalars(select(HrCensusRow).order_by(HrCensusRow.period)))
    payroll_rows = list(session.scalars(select(PayrollRegisterRow).order_by(PayrollRegisterRow.period)))
    latest: dict[str, HrCensusRow | PayrollRegisterRow] = {}
    listed: set[str] = set()
    for row in hr_rows:
        latest[row.employee_number] = row
        listed.add(row.employee_number)
    for row in payroll_rows:
        latest.setdefault(row.employee_number, row)
    for number in sorted(latest):
        source = latest[number]
        session.add(
            Person(
                employee_number=number,
                legal_name=source.legal_name,
                entity_code=source.entity_code,
                department=source.department,
                listed_in_hr=number in listed,
            )
        )


def _post_history(session: Session, actor: Actor) -> None:
    for period in ("2026-03", "2026-06"):
        drop = json.loads((fixture_dir() / f"{period}.json").read_text(encoding="utf-8"))
        for entity in ENTITIES:
            open_period(session, entity, period, actor)
        for entry in drop["ledger_entries"]:
            import_posted_entry(
                session,
                {
                    "entity_code": entry["entity_code"],
                    "period": period,
                    "memo": entry["memo"],
                    "lines": entry["lines"],
                },
                actor,
                source="northlight.prior_ledger",
            )
        if drop["close_period"]:
            for entity in ENTITIES:
                lock_period(session, entity, period, actor)
