from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class EvidenceEvent(Base):
    """One fact, call, decision or approval. Rows are never updated or deleted.

    hash is SHA-256 over every other field, prev_hash included, so editing any row
    breaks the chain from that row on.
    """

    __tablename__ = "evidence_events"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    at: Mapped[str] = mapped_column(String(40), nullable=False)
    sim_at: Mapped[str | None] = mapped_column(String(10))
    actor: Mapped[str] = mapped_column(String(48), nullable=False)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    case_id: Mapped[str | None] = mapped_column(String(36))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    basis_json: Mapped[str] = mapped_column(String, nullable=False)
    authority_row: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[str] = mapped_column(String, nullable=False)
    unverified_json: Mapped[str] = mapped_column(String, nullable=False)
    supersedes: Mapped[str | None] = mapped_column(String(16))
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class ErrorLog(Base):
    """An unexpected error: where it happened (file, line, function), why, and what was being done. Append-only."""

    __tablename__ = "error_log"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    at: Mapped[str] = mapped_column(String(40), nullable=False)
    where: Mapped[str] = mapped_column(String(300), nullable=False)
    error_type: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(String(2000), nullable=False)
    steps_json: Mapped[str] = mapped_column(String, nullable=False)
    request: Mapped[str | None] = mapped_column(String(300))
    actor: Mapped[str | None] = mapped_column(String(48))
    traceback: Mapped[str] = mapped_column(String, nullable=False)


class Precedent(Base):
    """A person's ruling that later cases follow. Expired or overruled, never deleted or edited.

    Overruling is a new precedent that names the one it replaces.
    """

    __tablename__ = "precedents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    rule: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_json: Mapped[str] = mapped_column(String, nullable=False)
    ruling: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(48), nullable=False)
    decided_on: Mapped[str] = mapped_column(String(10), nullable=False)
    valid_from: Mapped[str] = mapped_column(String(10), nullable=False)
    expires: Mapped[str | None] = mapped_column(String(10))
    overrules: Mapped[str | None] = mapped_column(ForeignKey("precedents.id"))
    source_document_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_id: Mapped[str] = mapped_column(String(16), nullable=False)
