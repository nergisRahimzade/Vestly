"""Signed grants and compensation actions that sit next to the monthly file drops."""

import hashlib

from sqlalchemy.orm import Session

from app.world.drops import actions_dir, grants_dir
from app.world.models import SourceDocument


def load_static_evidence(session: Session) -> list[SourceDocument]:
    rows = [
        _document("signed_grant", path.stem, path.read_text(encoding="utf-8"))
        for path in sorted(grants_dir().glob("*.txt"))
    ]
    rows += [
        _document("comp_action", f"Compensation actions {path.stem}", path.read_text(encoding="utf-8"))
        for path in sorted(actions_dir().glob("*.json"))
    ]
    session.add_all(rows)
    return rows


def _document(system: str, title: str, body: str) -> SourceDocument:
    return SourceDocument(
        system=system,
        period="static",
        title=title,
        body=body,
        content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        received_on="2026-03-01",
    )
