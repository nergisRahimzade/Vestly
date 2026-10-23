"""The generator builds any month, and each anomaly is a named plant."""

from decimal import Decimal

import pytest

from app.world.drops import (
    DEFAULT_PLANTS,
    MONTHS,
    SAM_ORTIZ,
    Plant,
    build_drop,
    ghost_on_register,
    month_of,
)


def _by_name(rows: list[dict], name: str) -> dict:
    return next(row for row in rows if row["legal_name"] == name)


def _balanced(entry: dict) -> bool:
    debits = sum(Decimal(line["debit"]) for line in entry["lines"])
    credits = sum(Decimal(line["credit"]) for line in entry["lines"])
    return debits == credits > 0


@pytest.mark.parametrize(
    ("period", "end", "label", "received_on"),
    [
        ("2026-02", "2026-02-28", "February 2026", "2026-03-02"),
        ("2028-02", "2028-02-29", "February 2028", "2028-03-02"),
        ("2026-04", "2026-04-30", "April 2026", "2026-05-02"),
        ("2026-12", "2026-12-31", "December 2026", "2027-01-02"),
    ],
)
def test_month_dates_are_computed(period, end, label, received_on):
    month = month_of(period)
    assert (month.end, month.label, month.received_on) == (end, label, received_on)


def test_a_bad_period_is_refused():
    for period in ("2026-13", "26-04", "2026-4"):
        with pytest.raises(ValueError):
            month_of(period)


def test_a_clean_month_has_no_anomalies():
    april = build_drop("2026-04")
    assert april["plants"] == []
    assert april["close_period"] is False
    assert not [doc for doc in april["documents"] if doc["system"] == "email"]
    names = {row["legal_name"] for row in april["payroll"]}
    assert "Priya Shah" not in names and "Sam Ortiz" not in names
    assert _by_name(april["hr"], "Priya Shah")["status"] == "terminated"
    assert _by_name(april["equity"], "Priya Shah")["platform_status"] == "forfeited"
    assert all(row["board_approved"] for row in april["equity"])
    rates = {"US": Decimal("0.04"), "UK": Decimal("0.05"), "DE": Decimal("0.20")}
    for row in april["payroll"]:
        assert Decimal(row["employer_contribution"]) == Decimal(row["gross_wages"]) * rates[row["entity_code"]]
    assert all(row["cleared"] for row in april["funding"])
    assert all(_balanced(entry) for entry in april["ledger_entries"])
    # Evan's April raise is not keyed until June, so April still pays the old rate.
    assert _by_name(april["payroll"], "Evan Brooks")["gross_wages"] == "10000.00"
    assert "Ines Duarte" not in {row["legal_name"] for row in april["hr"]}
    amira = _by_name(april["payroll"], "Amira Khan")
    assert amira["pay_period_start"] == "2026-03-26"
    assert amira["pay_period_end"] == "2026-04-25"
    tomas = _by_name(april["hr"], "Tomas Fischer")
    assert tomas["entity_code"] == "DE"


def test_a_closed_month_books_payroll_bank_and_equity():
    april = build_drop("2026-04", closed=True)
    kinds = {(entry["entity_code"], entry["kind"]) for entry in april["ledger_entries"]}
    assert {("US", "payroll"), ("US", "bank"), ("US", "equity")} <= kinds
    assert all(_balanced(entry) for entry in april["ledger_entries"])


def test_a_held_out_month_takes_any_plants():
    july = build_drop("2026-07", plants=[ghost_on_register(SAM_ORTIZ)])
    assert july["plants"] == ["ghost_on_register:PAY-0099"]
    assert "Sam Ortiz" in {row["legal_name"] for row in july["payroll"]}
    assert "Sam Ortiz" not in {row["legal_name"] for row in july["hr"]}
    evan = _by_name(july["payroll"], "Evan Brooks")
    assert evan["gross_wages"] == "12000.00"
    assert evan["retro_gross"] is None


def test_june_pays_evans_backdated_raise_as_a_lump():
    evan = _by_name(build_drop("2026-06")["payroll"], "Evan Brooks")
    assert evan["retro_effective_date"] == "2026-04-01"
    assert evan["retro_gross"] == "4000.00"
    assert evan["gross_wages"] == "16000.00"


def test_the_shipped_months_name_their_plants():
    for period in MONTHS:
        drop = build_drop(period)
        assert drop["plants"] == [plant.name for plant in DEFAULT_PLANTS[period]]
        assert all(_balanced(entry) for entry in drop["ledger_entries"])


def test_an_unknown_plant_stage_is_refused():
    with pytest.raises(ValueError):
        build_drop("2026-04", plants=[Plant("broken", "ledger", lambda drop: None)])


def test_ines_first_uk_cycle_is_partial():
    ines = _by_name(build_drop("2026-06")["payroll"], "Ines Duarte")
    assert ines["pay_period_start"] == "2026-06-02"
    assert ines["gross_wages"] == "4800.00"
    assert Decimal(ines["employer_contribution"]) == Decimal("240.00")
