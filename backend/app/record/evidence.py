"""The evidence log: every write path appends one row that answers "why" without asking anyone again."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.policy.loader import load_policy
from app.record.models import EvidenceEvent

GENESIS = "0" * 64
BODY_FIELDS = (
    "id",
    "seq",
    "at",
    "sim_at",
    "actor",
    "action",
    "subject_type",
    "subject_id",
    "case_id",
    "reason_code",
    "basis_json",
    "authority_row",
    "policy_version",
    "policy_hash",
    "payload_json",
    "unverified_json",
    "supersedes",
    "prev_hash",
)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def document_basis(content_hash: str, title: str | None = None, quote: str | None = None) -> dict:
    basis = {"type": "document", "doc_sha256": content_hash}
    if title is not None:
        basis["title"] = title
    if quote is not None:
        basis["quote"] = quote
    return basis


def rule_basis(rule_id: str) -> dict:
    return {"type": "rule", "id": rule_id}


def precedent_basis(precedent_id: str, decided_by: str, decided_on: str) -> dict:
    return {"type": "precedent", "id": precedent_id, "decided_by": decided_by, "on": decided_on}


def event_hash(row: EvidenceEvent) -> str:
    body = {name: getattr(row, name) for name in BODY_FIELDS}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def record(
    session: Session,
    actor: str,
    action: str,
    subject_type: str,
    subject_id: str,
    *,
    payload: dict | None = None,
    basis: Iterable[dict] = (),
    case_id: str | None = None,
    reason_code: str | None = None,
    authority_row: str | None = None,
    unverified: Iterable[str] = (),
    supersedes: str | None = None,
    sim_at: str | None = None,
) -> EvidenceEvent:
    """Append one event in the caller's transaction. It commits or rolls back with the write it explains."""
    policy = load_policy()
    last = session.scalar(select(EvidenceEvent).order_by(EvidenceEvent.seq.desc()).limit(1))
    seq = 1 if last is None else last.seq + 1
    row = EvidenceEvent(
        id=f"ev_{seq:06d}",
        seq=seq,
        at=datetime.now(timezone.utc).isoformat(),
        sim_at=sim_at,
        actor=actor,
        action=action,
        subject_type=subject_type,
        subject_id=str(subject_id),
        case_id=case_id,
        reason_code=reason_code,
        basis_json=canonical(list(basis)),
        authority_row=authority_row,
        policy_version=policy.version,
        policy_hash=policy.content_hash,
        payload_json=canonical(payload or {}),
        unverified_json=canonical(list(unverified)),
        supersedes=supersedes,
        prev_hash=GENESIS if last is None else last.hash,
    )
    row.hash = event_hash(row)
    session.add(row)
    # Flush now so the next event in this transaction chains onto this one.
    session.flush()
    return row


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    events: int
    first_broken: int | None
    reason: str | None


def verify(session: Session) -> ChainCheck:
    """Recompute every hash in order. The first row that does not match is where the record was changed."""
    previous_hash = GENESIS
    expected_seq = 1
    count = 0
    for row in session.scalars(select(EvidenceEvent).order_by(EvidenceEvent.seq)):
        count += 1
        if row.seq != expected_seq:
            return ChainCheck(False, count, expected_seq, f"Event {expected_seq} is missing.")
        if row.prev_hash != previous_hash:
            return ChainCheck(False, count, row.seq, f"Event {row.seq} does not point at the event before it.")
        if event_hash(row) != row.hash:
            return ChainCheck(False, count, row.seq, f"Event {row.seq} was changed after it was written.")
        previous_hash = row.hash
        expected_seq += 1
    return ChainCheck(True, count, None, None)


def as_dict(row: EvidenceEvent) -> dict:
    return {
        "id": row.id,
        "seq": row.seq,
        "at": row.at,
        "sim_at": row.sim_at,
        "actor": row.actor,
        "action": row.action,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "case_id": row.case_id,
        "reason_code": row.reason_code,
        "basis": json.loads(row.basis_json),
        "authority_row": row.authority_row,
        "policy_version": row.policy_version,
        "policy_hash": row.policy_hash,
        "payload": json.loads(row.payload_json),
        "unverified": json.loads(row.unverified_json),
        "supersedes": row.supersedes,
        "prev_hash": row.prev_hash,
        "hash": row.hash,
    }
