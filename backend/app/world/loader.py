"""Bring file drops into the database. Everything after this reads the database, never the fixture files."""

import hashlib
import json
import os
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
from app.world.drops import (
    COMPANY_CLOCK_START,
    HELD_OUT,
    MONTHS,
    SCENARIO_KINDS,
    build_company_drop,
    company_people,
    company_periods,
    fixture_dir,
    scenario_plants,
)
from app.world.evidence import load_static_evidence
from app.world.pdf import decode, is_pdf, pdf_text
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
# The cast world: 15 people, January to June 2026, June open. The unit tests' fixed scenario.
# The company world: about 300 people, January 2022 to October 2026, October open, aligned with today (D21, D22).
WORLDS = ("cast", "company")
COMPANY_VERSION = "2026-10"
# A loaded world, kept per process so the next fresh database restores it instead of loading 58 months again.
_SNAPSHOTS: dict[str, object] = {}
ENTITIES = tuple(code for code, _name, _currency in ENTITY_ROWS)
CURRENCY = {code: currency for code, _name, currency in ENTITY_ROWS}
DROP_SECTIONS = ("period", "received_on", "close_period", "rate_card", "hr", "payroll", "funding", "equity", "bank")


def selected_world() -> str:
    world = os.environ.get("VESTLY_WORLD", "company")
    if world not in WORLDS:
        raise LedgerError("unknown_world", f"VESTLY_WORLD must be one of {', '.join(WORLDS)}, not {world!r}.", 500)
    return world


def load_world(session: Session, actor: Actor, world: str | None = None, scenarios: list[dict] | None = None) -> dict:
    """Load a world's months, oldest first. The actor must be allowed to post and lock.

    The first load in a process is done month by month; a later load into a fresh database restores
    that result, which is the same database a full load would produce. Scenarios (a planted error of a
    kind, on a person, in a month) go into that month's drop; a world with scenarios is never snapshotted.
    """
    require(actor, WORLD_LOAD, "load the world")
    world = world or selected_world()
    if world not in WORLDS:
        raise LedgerError("unknown_world", f"No world named {world!r}.", 422)
    scenarios = [dict(item) for item in scenarios or []]
    _check_scenarios(world, scenarios)
    existing = session.scalar(select(WorldLoad).limit(1))
    if existing is not None:
        return {"status": "already_loaded", "world": existing.version.split(":")[0], "people": _people_count(session)}
    fresh = _is_fresh(session)
    if not scenarios and fresh and world in _SNAPSHOTS and _restore(session, world):
        return {"status": "loaded", "world": world, "people": _people_count(session), "restored": True}
    if world == "company":
        result = _load_company(session, actor, scenarios)
    else:
        result = _load_cast(session, actor, scenarios)
    if fresh and not scenarios:
        _snapshot(session, world)
    return {**result, "scenarios": scenarios}


def _check_scenarios(world: str, scenarios: list[dict]) -> None:
    """Every scenario names a known kind, a month of that world, and a person the plant can apply to."""
    from app.world.drops import COMPANY_FIRST, COMPANY_LAST, PEOPLE, SCENARIO_KINDS

    people = company_people() if world == "company" else PEOPLE
    first, last = (COMPANY_FIRST, COMPANY_LAST) if world == "company" else (min(MONTHS), max(MONTHS))
    for item in scenarios:
        if set(item) != {"kind", "employee_number", "period"}:
            raise LedgerError("invalid_scenario", "A scenario is {kind, employee_number, period}.", 422)
        if item["kind"] not in SCENARIO_KINDS:
            raise LedgerError("invalid_scenario", f"Unknown scenario kind {item['kind']!r}; choose one of {', '.join(SCENARIO_KINDS)}.", 422)
        if not first <= item["period"] <= last:
            raise LedgerError("invalid_scenario", f"The {world} world runs from {first} to {last}; {item['period']} is outside it.", 422)
        try:
            scenario_plants(item["kind"], item["employee_number"], item["period"], people)
        except ValueError as exc:
            raise LedgerError("invalid_scenario", str(exc), 422) from exc


def _plants_for(scenarios: list[dict], period: str, people: list[dict]) -> list:
    return [plant for item in scenarios if item["period"] == period for plant in scenario_plants(item["kind"], item["employee_number"], period, people)]


def _load_cast(session: Session, actor: Actor, scenarios: list[dict] | None = None) -> dict:
    static = load_static_evidence(session, "cast")
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
    from app.world.drops import DEFAULT_PLANTS, PEOPLE, build_drop

    for period in sorted(MONTHS):
        with doing("loading the shipped world", period=period):
            extra = _plants_for(scenarios or [], period, PEOPLE)
            drop = build_drop(period, DEFAULT_PLANTS.get(period, []) + extra) if extra else read_drop_file(period)
            load_drop(session, drop, actor)
    seed_march_ruling(session, actor)
    session.add(WorldLoad(version=f"cast:{WORLD_VERSION}", loaded_at=datetime.now(timezone.utc), scenarios=json.dumps(scenarios or [])))
    session.commit()
    return {"status": "loaded", "world": "cast", "people": _people_count(session)}


def _load_company(session: Session, actor: Actor, scenarios: list[dict] | None = None) -> dict:
    """The whole company: 58 monthly drops built from the generator, then the clock set to October's close day."""
    from app.controls.models import SimClock

    with doing("loading the company's signed grants and compensation actions"):
        static = load_static_evidence(session, "company")
        session.flush()
    record(
        session,
        actor.handle,
        "documents_received",
        "documents",
        "static",
        payload={"count": len(static), "world": "company"},
        basis=[document_basis(row.content_hash, row.title) for row in static[:50]],
    )
    session.commit()
    people = company_people()
    for period in company_periods():
        with doing("loading the company world", period=period):
            load_drop(session, build_company_drop(period, _plants_for(scenarios or [], period, people)), actor)
    seed_march_ruling(session, actor)
    clock = session.get(SimClock, 1)
    if clock is None:
        session.add(SimClock(id=1, today=COMPANY_CLOCK_START))
    else:
        clock.today = COMPANY_CLOCK_START
    session.add(WorldLoad(version=f"company:{COMPANY_VERSION}", loaded_at=datetime.now(timezone.utc), scenarios=json.dumps(scenarios or [])))
    session.commit()
    return {"status": "loaded", "world": "company", "people": _people_count(session)}


def _is_fresh(session: Session) -> bool:
    from app.record.models import EvidenceEvent

    return (
        session.scalar(select(func.count()).select_from(DropLoad)) == 0
        and session.scalar(select(func.count()).select_from(EvidenceEvent)) == 0
    )


def _snapshot(session: Session, world: str) -> None:
    """Keep a copy of the freshly loaded database (SQLite only) for the next fresh load in this process."""
    import sqlite3

    engine = session.get_bind()
    if engine.dialect.name != "sqlite":
        return
    session.commit()
    copy = sqlite3.connect(":memory:", check_same_thread=False)
    raw = engine.raw_connection()
    try:
        with doing("snapshotting the loaded world", world=world):
            raw.driver_connection.backup(copy)
    finally:
        raw.close()
    _SNAPSHOTS[world] = copy


def _restore(session: Session, world: str) -> bool:
    engine = session.get_bind()
    if engine.dialect.name != "sqlite":
        return False
    session.commit()
    raw = engine.raw_connection()
    try:
        with doing("restoring the loaded world", world=world):
            _SNAPSHOTS[world].backup(raw.driver_connection)
    finally:
        raw.close()
    session.expire_all()
    return True


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
    """The month's rates: contribution rates and the management pack from the drop, and the reference rates
    from Open Exchange Rates at the month's last day (D20). Without a stored month-end rate the card has no
    reference rates at all, so nothing can be translated or booked at a rate that is not the month's own."""
    from app.fx.store import month_reference

    row = session.get(DropLoad, period)
    if row is None:
        return None
    card = {key: value for key, value in json.loads(row.rate_card).items() if not key.endswith("_reference")}
    real = month_reference(session, period)
    if real is not None:
        card.update(real)
    return card


def receive_month(session: Session, period: str, actor: Actor) -> dict:
    """A held-out month arrives (D27): only in the company world, only after the month before it, and only once
    the simulated clock has reached the day its drop comes. Its rates are whatever exists by then."""
    from app.controls.clock import today
    from app.periods import add_months, month_of

    require(actor, WORLD_LOAD, "receive a month")
    if period not in HELD_OUT:
        raise LedgerError("not_held_out", f"{period} is not a held-out month; those are {', '.join(HELD_OUT)}.", 422)
    world = session.scalar(select(WorldLoad).limit(1))
    if world is None or not world.version.startswith("company:"):
        raise LedgerError("wrong_world", "Held-out months belong to the company world.", 409)
    before = add_months(f"{period}-01", -1)[:7]
    if session.get(DropLoad, before) is None:
        raise LedgerError("month_out_of_order", f"{period} arrives after {before}, which has not arrived.", 409)
    arrives = month_of(period).received_on
    if today(session) < arrives:
        raise LedgerError("not_arrived", f"The {period} drop arrives on {arrives}; the clock says {today(session)}.", 409)
    with doing("receiving a held-out month", period=period):
        return load_drop(session, build_company_drop(period, _scenarios_for(session, period)), actor)


def _scenarios_for(session: Session, period: str) -> list:
    """Scenarios recorded for this world at load time apply to their month's drop, wherever it arrives."""
    world = session.scalar(select(WorldLoad).limit(1))
    plants = []
    for item in json.loads(world.scenarios or "[]") if world is not None else []:
        if item["period"] == period:
            plants += scenario_plants(item["kind"], item["employee_number"], period, company_people())
    return plants


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
        if is_pdf(extra["body"]):
            # The PDF is the source; the text a parser reads from it is stored beside it, naming the PDF's hash.
            original = documents.pop(extra["title"])
            original.system = "pdf"
            documents[f"pdf:{extra['title']}"] = original
            with doing("reading the text of a PDF", title=extra["title"], period=period):
                text = pdf_text(decode(extra["body"]))
            title = f"{extra['title']} · text read from PDF sha256:{original.content_hash}"
            documents[title] = _document(extra["system"], period, title, text, received_on)
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
