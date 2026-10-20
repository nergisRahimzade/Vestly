import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

import fx


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point fx at a fresh temporary database for each test."""
    path = str(tmp_path / "test.db")
    monkeypatch.setattr(fx, "DB_PATH", path)
    fx.init_db()
    return path


def insert_rates(path, fetched_at, rates):
    with sqlite3.connect(path) as db:
        db.executemany(
            "INSERT INTO fx_rates VALUES (?, ?, ?, ?)",
            [(fetched_at, "USD", cur, rate) for cur, rate in rates.items()],
        )


def now_iso(hours_ago=0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_eur_to_usd(db_path):
    insert_rates(db_path, now_iso(), {"EUR": 0.9, "GBP": 0.75})
    result = fx.convert(100, "EUR", "USD")
    assert result["amount"] == pytest.approx(111.11)       # 100 / 0.9


def test_usd_to_eur(db_path):
    insert_rates(db_path, now_iso(), {"EUR": 0.8, "GBP": 0.5})
    result = fx.convert(100, "USD", "EUR")
    assert result["amount"] == pytest.approx(80.0)         # 100 * 0.8


def test_cross_currency_eur_to_gbp(db_path):
    insert_rates(db_path, now_iso(), {"EUR": 0.8, "GBP": 0.5})
    result = fx.convert(100, "EUR", "GBP")
    assert result["amount"] == pytest.approx(62.5)         # 100 / 0.8 * 0.5
    assert result["rate_used"] == pytest.approx(0.625)


def test_same_currency_returns_same_amount(db_path):
    insert_rates(db_path, now_iso(), {"EUR": 0.9})
    assert fx.convert(250, "USD", "USD")["amount"] == 250.0


def test_latest_rate_wins(db_path):
    insert_rates(db_path, now_iso(2), {"EUR": 0.8})
    insert_rates(db_path, now_iso(1), {"EUR": 1.0})
    assert fx.convert(100, "EUR", "USD")["amount"] == pytest.approx(100.0)


def test_as_of_uses_rate_from_that_time(db_path):
    insert_rates(db_path, "2026-10-01T00:00:00+00:00", {"EUR": 0.8})
    insert_rates(db_path, "2026-10-02T00:00:00+00:00", {"EUR": 1.0})
    result = fx.convert(100, "EUR", "USD", as_of="2026-10-01T12:00:00+00:00")
    assert result["amount"] == pytest.approx(125.0)        # 100 / 0.8


def test_fresh_rates_have_no_warning(db_path):
    insert_rates(db_path, now_iso(1), {"EUR": 0.9})
    assert fx.convert(100, "EUR", "USD")["warning"] is None


def test_stale_rates_trigger_warning(db_path):
    insert_rates(db_path, now_iso(5), {"EUR": 0.9})        # older than MAX_AGE_HOURS (3)
    assert fx.convert(100, "EUR", "USD")["warning"] is not None


def test_missing_rate_raises(db_path):
    with pytest.raises(ValueError):
        fx.convert(100, "EUR", "USD")                      # table is empty


def test_failed_fetch_keeps_old_rates(db_path, monkeypatch):
    insert_rates(db_path, now_iso(), {"EUR": 0.9})

    def broken_fetch():
        raise RuntimeError("API down")

    monkeypatch.setattr(fx, "fetch_rates", broken_fetch)
    fx.update_rates()                                      # should not raise

    with sqlite3.connect(db_path) as db:
        count = db.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
    assert count == 1                                      # nothing new saved
    assert fx.convert(100, "EUR", "USD")["amount"] == pytest.approx(111.11)