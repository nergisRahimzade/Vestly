from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.identities import IDENTITIES
from app.auth.models import Identity
from app.auth.tokens import token_for, token_hash
from app.ledger.chart import ACCOUNTS, DEPARTMENTS, ENTITIES
from app.ledger.models import Account, Entity
from app.world.models import Department


def seed(session: Session) -> None:
    for code, name, currency in ENTITIES:
        if session.scalar(select(Entity).where(Entity.code == code)) is None:
            session.add(Entity(code=code, name=name, currency=currency))
    for code, name, account_type, normal_balance in ACCOUNTS:
        if session.scalar(select(Account).where(Account.code == code)) is None:
            session.add(
                Account(
                    code=code,
                    name=name,
                    account_type=account_type,
                    normal_balance=normal_balance,
                )
            )
    for name in DEPARTMENTS:
        if session.scalar(select(Department).where(Department.name == name)) is None:
            session.add(Department(name=name))
    for handle, display_name, role, department in IDENTITIES:
        row = session.get(Identity, handle)
        digest = token_hash(token_for(handle))
        if row is None:
            session.add(
                Identity(
                    handle=handle,
                    display_name=display_name,
                    role=role,
                    department=department,
                    token_hash=digest,
                )
            )
        else:
            row.role = role
            row.department = department
            row.token_hash = digest
    session.flush()
