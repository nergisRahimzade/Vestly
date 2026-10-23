"""Precedents: what a person ruled once, applied again where its scope and dates say so.

Matching is structured, not a text search. A precedent's scope names the keys it is
limited to (employee, entity, fact); an empty scope covers the whole rule. A case
matches when it has every key the scope names, with the same value.
"""

import json
import re
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.auth.permissions import PRECEDENT_RULE, require
from app.ledger.errors import LedgerError
from app.record.evidence import canonical, document_basis, record
from app.record.models import Precedent
from app.world.models import SourceDocument

SCOPE_KEYS = ("employee", "entity", "fact")
RULE_ID = re.compile(r"^[A-Z]{3}-\d+$")

JONAH_RULING_TITLE = "Controller rejection of Jonah Hale bank change"
MARCH_PRECEDENT = "P-2026-03-001"


def rule_precedent(
    session: Session,
    actor: Actor,
    *,
    rule: str,
    scope: dict,
    ruling: str,
    reason: str,
    valid_from: str,
    decided_on: str | None = None,
    expires: str | None = None,
    overrules: str | None = None,
    source_document_hash: str | None = None,
    precedent_id: str | None = None,
) -> Precedent:
    """The controller turns a ruling into a precedent. Overruling an old one is a new precedent that names it."""
    require(actor, PRECEDENT_RULE, "rule a precedent")
    if not RULE_ID.match(rule):
        raise LedgerError("invalid_rule", f"{rule!r} is not a policy rule id such as BNK-1.", 422)
    unknown = sorted(set(scope) - set(SCOPE_KEYS))
    if unknown:
        raise LedgerError("invalid_scope", f"Scope keys must be among {', '.join(SCOPE_KEYS)}; got {unknown}.", 422)
    if not ruling.strip() or not reason.strip():
        raise LedgerError("invalid_precedent", "A precedent needs a ruling and a reason.", 422)
    for label, value in (("valid_from", valid_from), ("decided_on", decided_on), ("expires", expires)):
        if value is not None:
            _require_date(label, value)
    if expires is not None and expires <= valid_from:
        raise LedgerError("invalid_precedent", "A precedent must expire after it starts.", 422)
    if overrules is not None:
        target = session.get(Precedent, overrules)
        if target is None:
            raise LedgerError("unknown_precedent", f"No precedent {overrules} to overrule.", 404)
        if session.scalar(select(Precedent).where(Precedent.overrules == overrules)) is not None:
            raise LedgerError("already_overruled", f"{overrules} has already been overruled.", 409)
    if source_document_hash is not None:
        document = session.scalar(select(SourceDocument).where(SourceDocument.content_hash == source_document_hash))
        if document is None:
            raise LedgerError("unknown_document", "No stored document has that hash.", 422)
    precedent_id = precedent_id or _next_id(session, valid_from)
    if session.get(Precedent, precedent_id) is not None:
        raise LedgerError("precedent_exists", f"{precedent_id} already exists.", 409)
    basis = [document_basis(source_document_hash)] if source_document_hash else []
    event = record(
        session,
        actor.handle,
        "precedent_ruled",
        "precedent",
        precedent_id,
        payload={
            "rule": rule,
            "scope": scope,
            "ruling": ruling,
            "reason": reason,
            "valid_from": valid_from,
            "expires": expires,
            "overrules": overrules,
        },
        basis=basis,
    )
    row = Precedent(
        id=precedent_id,
        rule=rule,
        scope_json=canonical(scope),
        ruling=ruling.strip(),
        reason=reason.strip(),
        decided_by=actor.handle,
        decided_on=decided_on or valid_from,
        valid_from=valid_from,
        expires=expires,
        overrules=overrules,
        source_document_hash=source_document_hash,
        evidence_id=event.id,
    )
    session.add(row)
    session.commit()
    return row


def search_precedents(
    session: Session,
    on: str,
    *,
    rule: str | None = None,
    employee: str | None = None,
    entity: str | None = None,
    fact: str | None = None,
) -> list[Precedent]:
    """Precedents in force on a date whose scope the case satisfies."""
    _require_date("on", on)
    case = {"employee": employee, "entity": entity, "fact": fact}
    rows = list(session.scalars(select(Precedent).order_by(Precedent.valid_from, Precedent.id)))
    overruled_by = {row.overrules: row for row in rows if row.overrules is not None}
    matches = []
    for row in rows:
        if rule is not None and row.rule != rule:
            continue
        if row.valid_from > on or (row.expires is not None and on >= row.expires):
            continue
        replacement = overruled_by.get(row.id)
        if replacement is not None and replacement.valid_from <= on:
            continue
        scope = json.loads(row.scope_json)
        if all(case.get(key) is not None and case[key] == value for key, value in scope.items()):
            matches.append(row)
    return matches


def as_dict(row: Precedent) -> dict:
    return {
        "id": row.id,
        "rule": row.rule,
        "scope": json.loads(row.scope_json),
        "ruling": row.ruling,
        "reason": row.reason,
        "decided_by": row.decided_by,
        "decided_on": row.decided_on,
        "valid_from": row.valid_from,
        "expires": row.expires,
        "overrules": row.overrules,
        "source_document_hash": row.source_document_hash,
        "evidence_id": row.evidence_id,
    }


def seed_march_ruling(session: Session, controller: Actor) -> Precedent | None:
    """The controller's March rejection of Jonah's emailed bank change, made a precedent from the email itself."""
    if session.get(Precedent, MARCH_PRECEDENT) is not None:
        return None
    email = session.scalar(select(SourceDocument).where(SourceDocument.title == JONAH_RULING_TITLE))
    if email is None:
        return None
    return rule_precedent(
        session,
        controller,
        rule="BNK-1",
        scope={"entity": "US"},
        ruling="Reject bank-detail changes that arrive without a call-back; the HR bank master stands.",
        reason="Jonah Hale's request came only by email; HR had not updated the bank master and no second person approved.",
        decided_on="2026-03-06",
        valid_from="2026-03-06",
        source_document_hash=email.content_hash,
        precedent_id=MARCH_PRECEDENT,
    )


def _next_id(session: Session, valid_from: str) -> str:
    prefix = f"P-{valid_from[:4]}-{valid_from[5:7]}-"
    count = session.scalar(select(func.count()).select_from(Precedent).where(Precedent.id.startswith(prefix)))
    return f"{prefix}{int(count or 0) + 1:03d}"


def _require_date(label: str, value: str) -> None:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        raise LedgerError("invalid_date", f"{label} must be YYYY-MM-DD.", 422) from None
