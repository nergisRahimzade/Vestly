"""Postgres enforces balance and the period lock even if the service is bypassed."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db import Base, build_engine, init_db, postgres_available

ADMIN_URL = os.environ.get(
    "POSTGRES_ADMIN_URL",
    "postgresql+psycopg://vestly:vestly@localhost:5432/vestly",
)
TEST_URL = os.environ.get(
    "POSTGRES_TEST_URL",
    "postgresql+psycopg://vestly:vestly@localhost:5432/vestly_test",
)


@pytest.fixture(scope="module")
def pg_engine():
    if not postgres_available(ADMIN_URL):
        pytest.skip("Postgres is not running")
    admin = build_engine(ADMIN_URL)
    with admin.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname = 'vestly_test'")).scalar()
        if exists is None:
            connection.execute(text("CREATE DATABASE vestly_test"))
    admin.dispose()
    engine = build_engine(TEST_URL)
    Base.metadata.drop_all(engine)
    init_db(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def _ids(connection):
    return connection.execute(
        text(
            """
            SELECT e.id AS entity_id, p.id AS period_id, a.id AS expense_id, b.id AS payable_id
            FROM entities e
            JOIN periods p ON p.entity_id = e.id
            JOIN accounts a ON a.code = '5000'
            JOIN accounts b ON b.code = '2100'
            WHERE e.code = 'US'
            """
        )
    ).one()


def _insert_entry(connection, ids, number: str, credit: str) -> None:
    entry_id = str(uuid4())
    connection.execute(
        text(
            """
            INSERT INTO journal_entries
              (id, entity_id, period_id, number, memo, status, currency,
               prepared_by, prepared_at, reviewed_by, reviewed_at, posted_by, posted_at)
            VALUES
              (:id, :entity_id, :period_id, :number, 'raw sql', 'posted', 'USD',
               'vestly', :posted_at, 'dana.ruiz', :posted_at, 'dana.ruiz', :posted_at)
            """
        ),
        {
            "id": entry_id,
            "entity_id": ids.entity_id,
            "period_id": ids.period_id,
            "number": number,
            "posted_at": datetime.now(timezone.utc),
        },
    )
    for line_number, account_id, debit, line_credit in (
        (1, ids.expense_id, "100.00", "0.00"),
        (2, ids.payable_id, "0.00", credit),
    ):
        connection.execute(
            text(
                """
                INSERT INTO journal_lines
                  (id, entry_id, account_id, line_number, debit, credit, department)
                VALUES
                  (:id, :entry_id, :account_id, :line_number, :debit, :credit, :department)
                """
            ),
            {
                "id": str(uuid4()),
                "entry_id": entry_id,
                "account_id": account_id,
                "line_number": line_number,
                "debit": debit,
                "credit": line_credit,
                "department": "Engineering" if line_number == 1 else None,
            },
        )


def test_postgres_rejects_unbalanced_entries_and_locked_periods(pg_engine):
    factory = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        from app.auth.identities import Actor
        from app.ledger.service import open_period

        open_period(session, "US", "2026-03", Actor(handle="dana.ruiz", role="controller"))

    with pg_engine.begin() as connection:
        ids = _ids(connection)
        _insert_entry(connection, ids, "JE-PG-BALANCED", "100.00")

    with pytest.raises(IntegrityError, match="unbalanced_journal"):
        with pg_engine.begin() as connection:
            ids = _ids(connection)
            _insert_entry(connection, ids, "JE-PG-UNBALANCED", "90.00")

    with factory() as session:
        from app.auth.identities import Actor
        from app.ledger.service import lock_period

        lock_period(session, "US", "2026-03", Actor(handle="dana.ruiz", role="controller"))

    with pytest.raises(IntegrityError, match="period_locked"):
        with pg_engine.begin() as connection:
            ids = _ids(connection)
            _insert_entry(connection, ids, "JE-PG-LOCKED", "100.00")


def test_postgres_keeps_separation_of_duties_and_history(pg_engine):
    factory = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        from app.auth.identities import Actor
        from app.ledger.service import open_period

        open_period(session, "US", "2026-06", Actor(handle="dana.ruiz", role="controller"))

    with pg_engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT e.id AS entity_id, p.id AS period_id, a.id AS expense_id, b.id AS payable_id
                FROM entities e
                JOIN periods p ON p.entity_id = e.id AND p.year = 2026 AND p.month = 6
                JOIN accounts a ON a.code = '5000'
                JOIN accounts b ON b.code = '2100'
                WHERE e.code = 'US'
                """
            )
        ).one()
        connection.execute(
            text(
                """
                INSERT INTO journal_entries
                  (id, entity_id, period_id, number, memo, status, currency, prepared_by, prepared_at)
                VALUES
                  ('pg-draft', :entity_id, :period_id, 'JE-PG-DRAFT', 'raw draft', 'draft', 'USD', 'vestly', now())
                """
            ),
            {"entity_id": row.entity_id, "period_id": row.period_id},
        )
        for line_number, account_id, debit, credit in (
            (1, row.expense_id, "5.00", "0.00"),
            (2, row.payable_id, "0.00", "5.00"),
        ):
            connection.execute(
                text(
                    "INSERT INTO journal_lines (id, entry_id, account_id, line_number, debit, credit) "
                    "VALUES (:id, 'pg-draft', :account_id, :line_number, :debit, :credit)"
                ),
                {
                    "id": str(uuid4()),
                    "account_id": account_id,
                    "line_number": line_number,
                    "debit": debit,
                    "credit": credit,
                },
            )

    with pytest.raises(IntegrityError, match="preparer_is_not_reviewer"):
        with pg_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE journal_entries SET status = 'approved', reviewed_by = prepared_by, "
                    "reviewed_at = now() WHERE id = 'pg-draft'"
                )
            )

    with pytest.raises(IntegrityError, match="journal_is_final"):
        with pg_engine.begin() as connection:
            connection.execute(text("UPDATE journal_entries SET memo = 'edited' WHERE number = 'JE-PG-BALANCED'"))

    with pg_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO reconciliation_runs
                  (id, period, sequence, ran_by, ran_at, policy_version, policy_hash, inputs_hash, input_documents)
                VALUES ('pg-run', '2026-06', 1, 'vestly', now(), 'v', 'p', 'i', '[]')
                """
            )
        )
    with pytest.raises(IntegrityError, match="append_only"):
        with pg_engine.begin() as connection:
            connection.execute(text("DELETE FROM reconciliation_runs WHERE id = 'pg-run'"))
