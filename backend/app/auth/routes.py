"""The login picker (D10): pick a Northlight person, get their token. The role comes from the server, never the caller.

This stands in for single sign-on in the demo. Vestly's own seats cannot be picked: nobody in the UI acts as
Vestly. Set VESTLY_DEMO_LOGIN=0 to turn the picker off; tokens are then handed out of band, as in the tests.
"""

import os

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.models import Identity
from app.auth.permissions import ROLE_PERMISSIONS
from app.auth.tokens import token_for, token_hash
from app.controls.clock import today
from app.db import get_db
from app.ledger.errors import LedgerError
from app.record.evidence import record

router = APIRouter(prefix="/auth")


class LoginIn(BaseModel):
    handle: str


def _picker_on() -> bool:
    return os.environ.get("VESTLY_DEMO_LOGIN", "1") != "0"


def _person(row: Identity) -> dict:
    return {
        "handle": row.handle,
        "display_name": row.display_name,
        "role": row.role,
        "department": row.department,
        "permissions": sorted(ROLE_PERMISSIONS.get(row.role, frozenset())),
    }


@router.get("/identities")
def identities(db: Session = Depends(get_db)) -> list[dict]:
    """Who can sign in through the picker: every person, none of Vestly's seats."""
    if not _picker_on():
        raise LedgerError("login_disabled", "The login picker is off (VESTLY_DEMO_LOGIN=0).", 404)
    rows = db.scalars(select(Identity).where(Identity.role != "agent").order_by(Identity.role, Identity.handle))
    return [_person(row) for row in rows]


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)) -> dict:
    """Issue the picked person's token and log the sign-in. The token, not the UI, carries the identity from here."""
    if not _picker_on():
        raise LedgerError("login_disabled", "The login picker is off (VESTLY_DEMO_LOGIN=0).", 404)
    row = db.get(Identity, body.handle)
    if row is None:
        raise LedgerError("unknown_identity", f"No Northlight identity {body.handle!r}.", 404)
    if row.role == "agent":
        raise LedgerError("forbidden", f"{row.handle} is a Vestly seat; nobody signs in as Vestly.", 403, rule="ACC-1", actor=row.handle, action="sign in as an agent seat")
    token = token_for(row.handle)
    if token_hash(token) != row.token_hash:
        raise LedgerError("token_mismatch", f"The token for {row.handle} does not match the stored hash; was VESTLY_TOKEN_SECRET changed after seeding?", 500)
    record(db, row.handle, "signed_in", "identity", row.handle, payload={"via": "login picker", "role": row.role}, sim_at=today(db))
    db.commit()
    return {"token": token, **_person(row)}


@router.get("/me")
def me(db: Session = Depends(get_db), who: Actor = Depends(current_actor)) -> dict:
    return _person(db.get(Identity, who.handle))
