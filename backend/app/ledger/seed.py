from sqlalchemy import select
from sqlalchemy.orm import Session

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
    session.flush()
