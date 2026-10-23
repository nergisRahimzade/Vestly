from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identities import Actor
from app.auth.models import Identity
from app.auth.tokens import token_hash
from app.db import get_db
from app.ledger.errors import LedgerError


def current_actor(
    authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
) -> Actor:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise LedgerError("unauthenticated", "Send Authorization: Bearer <token>.", 401)
    row = db.scalar(select(Identity).where(Identity.token_hash == token_hash(token.strip())))
    if row is None:
        raise LedgerError("unauthenticated", "That token does not belong to a Northlight identity.", 401)
    return Actor(handle=row.handle, role=row.role)
