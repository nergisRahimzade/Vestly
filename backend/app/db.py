import os
from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite+pysqlite:///./vestly.db")


def build_engine(url: str):
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    elif url.startswith("postgresql"):
        kwargs["connect_args"] = {"connect_timeout": 3}
    return create_engine(url, **kwargs)


ENGINE = build_engine(database_url())
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False)


@event.listens_for(ENGINE, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    if ENGINE.dialect.name == "sqlite":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db(engine=None) -> None:
    engine = engine or ENGINE
    from app.auth import models as auth_models  # noqa: F401
    from app.ledger import models  # noqa: F401
    from app.world import models as world_models  # noqa: F401
    from app.reconcile import models as reconcile_models  # noqa: F401
    from app.equity import models as equity_models  # noqa: F401
    from app.record import models as record_models  # noqa: F401
    from app.ledger.guards import install_guards
    from app.ledger.seed import seed

    Base.metadata.create_all(engine)
    install_guards(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        seed(session)
        session.commit()


def postgres_available(url: str) -> bool:
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return False
    finally:
        engine.dispose()
    return True
