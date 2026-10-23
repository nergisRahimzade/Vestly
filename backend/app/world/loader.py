"""Bring file drops into the database. Everything after this reads the database, never the fixture files."""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.auth.permissions import WORLD_LOAD, require
from app.errors import ContextError, doing
from app.ledger.chart import ENTITIES as ENTITY_ROWS
from app.ledger.errors import LedgerError
from app.ledger.service import import_posted_entry, lock_period, open_period, parse_period
from app.record.evidence import document_basis, record
from app.record.precedents import seed_march_ruling
from app.world.drops import MONTHS, fixture_dir
from app.world.evidence import load_static_evidence
from app.world.models import (
    BankLine,
    DropLoad,
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
ENTITIES = tuple(code for code, _name, _currency in ENTITY_ROWS)
CURRENCY = {code: currency for code, _name, currency in ENTITY_ROWS}
DROP_SECTIONS = ("period", "received_on", "close_period", "rate_card", "hr", "payroll", "funding", "equity", "bank")


def load_world(session: Session, actor: Actor) -> dict:
    """Load the months the test world ships with, oldest first. The actor must be allowed to post and lock."""
    if session.get(WorldLoad, WORLD_VERSION) is not None:
        return {"status": "already_loaded", "people": _people_count(session)}
    static = load_static_evidence(session)
    session.flush()
    record(
        session,
        actor.handle,
        "documents_received",
        "documents",
        "static",
        payload={"count": len(static)},
        basis=[document_basis(row.content_hash, row.title) for row in static],
    )
    session.commit()
    for period in sorted(MONTHS):
        with doing("loading the shipped world", period=period):
            load_drop(session, read_drop_file(period), actor)
    seed_march_ruling(session, actor)
    session.add(WorldLoad(version=WORLD_VERSION, loaded_at=datetime.now(timezone.utc)))
    session.commit()
    return {"status": "loaded", "people": _people_count(session)}


def read_drop_file(period: str) -> dict:
    path = fixture_dir() / f"{period}.json"
    with doing("reading the file drop", file=path.name):
        return json.loads(path.read_text(encoding="utf-8"))


def load_drop(session: Session, drop: dict, actor: Actor) -> dict:
    """Store one month's drop, book what was already on the books, and lock the month if it arrived closed.

    Loading the same drop twice does nothing. A different drop for a month already loaded is refused.
    """
    require(actor, WORLD_LOAD, "load a file drop")
    missing = [key for key in DROP_SECTIONS if key not in drop]
    if missing:
        raise LedgerError("invalid_drop", f"The drop is missing {', '.join(missing)}.", 422)
    period = str(drop["period"])
    parse_period(period)
    digest = drop_hash(drop)
    existing = session.get(DropLoad, period)
    if existing is not None:
        if existing.content_hash == digest:
            return {"status": "already_loaded", "period": period}
        raise LedgerError("drop_conflict", f"A different {period} drop is already loaded.", 409)
    try:
        documents = store_drop(session, drop)
    except ContextError as exc:
        session.rollback()
        raise LedgerError("invalid_drop", f"The {period} drop could not be stored, {exc.describe()}", 422) from exc
    record(
        session,
        actor.handle,
        "drop_received",
        "drop",
        period,
        payload={"content_hash": digest, "closed": bool(drop["close_period"]), "received_on": drop["received_on"]},
        basis=[document_basis(row.content_hash, row.title) for row in documents],
    )
    session.add(
        DropLoad(
            period=period,
            content_hash=digest,
            received_on=drop["received_on"],
            closed=bool(drop["close_period"]),
            rate_card=json.dumps(drop["rate_card"], sort_keys=True),
            loaded_by=actor.handle,
            loaded_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    _refresh_people(session)
    session.commit()
    with doing("booking the old process's ledger history", period=period):
        _book_history(session, drop, actor)
    return {"status": "loaded", "period": period, "closed": bool(drop["close_period"])}


def drop_hash(drop: dict) -> str:
    return hashlib.sha256(json.dumps(drop, sort_keys=True).encode("utf-8")).hexdigest()


def world_loaded(session: Session) -> bool:
    return session.scalar(select(func.count()).select_from(DropLoad)) > 0


def first_loaded_period(session: Session) -> str | None:
    return session.scalar(select(func.min(DropLoad.period)))


def rate_card_for(session: Session, period: str) -> dict | None:
    row = session.get(DropLoad, period)
    return None if row is None else json.loads(row.rate_card)


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


def store_drop(session: Session, drop: dict) -> list[SourceDocument]:
    """Store one month's file drop as source rows. It does not post anything."""
    period = drop["period"]
    received_on = drop["received_on"]
    sections = {
        "hr": drop["hr"],
        "payroll": drop["payroll"],
        "funding": drop["funding"],
        "equity": drop["equity"],
        "bank": drop["bank"],
        "ledger": drop.get("ledger_entries", []),
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
    for extra in drop.get("documents", []):
        documents[extra["title"]] = _document(extra["system"], period, extra["title"], extra["body"], received_on)
    session.add_all(documents.values())
    session.flush()
    hr = documents["hr"]
    payroll = documents["payroll"]
    equity = documents["equity"]
    bank = documents["bank"]
    ledger = documents["ledger"]
    for section, model, document in (
        ("hr", HrCensusRow, hr),
        ("payroll", PayrollRegisterRow, payroll),
        ("funding", FundingInstruction, payroll),
        ("equity", EquitySnapshot, equity),
        ("bank", BankLine, bank),
    ):
        for index, row in enumerate(drop[section], start=1):
            with doing(f"storing the {section} section", period=period, row=index):
                session.add(model(document_id=document.id, period=period, **row))
    for index, entry in enumerate(drop.get("ledger_entries", []), start=1):
        with doing("storing the ledger section", period=period, entry=index):
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
    with doing("writing the drop's rows to the database", period=period):
        session.flush()
    return list(documents.values())


def _refresh_people(session: Session) -> None:
    """Everyone any source file names. HR's latest row wins; someone only payroll knows stays unlisted in HR."""
    latest: dict[str, HrCensusRow | PayrollRegisterRow] = {}
    listed: set[str] = set()
    for row in session.scalars(select(PayrollRegisterRow).order_by(PayrollRegisterRow.period)):
        latest[row.employee_number] = row
    for row in session.scalars(select(HrCensusRow).order_by(HrCensusRow.period)):
        latest[row.employee_number] = row
        listed.add(row.employee_number)
    existing = {row.employee_number: row for row in session.scalars(select(Person))}
    for number in sorted(latest):
        source = latest[number]
        person = existing.get(number)
        if person is None:
            person = Person(employee_number=number)
            session.add(person)
        person.legal_name = source.legal_name
        person.entity_code = source.entity_code
        person.department = source.department
        person.listed_in_hr = number in listed


def _book_history(session: Session, drop: dict, actor: Actor) -> None:
    period = drop["period"]
    for entity in ENTITIES:
        open_period(session, entity, period, actor)
    for entry in drop.get("ledger_entries", []):
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
