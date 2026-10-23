"""Materiality as a key concept: the line between "Vestly fixes it" and "Vestly asks a person"."""

from decimal import Decimal

import pytest

from app.policy.loader import load_policy
from app.policy.materiality import ACT_ALONE, ESCALATE, assess, rate_to

POLICY = load_policy()
GBP_USD = Decimal("1.2750")


def test_the_threshold_is_written_in_policy():
    assert POLICY.materiality_absolute == Decimal("25.00")
    assert POLICY.materiality_currency == "USD"
    assert set(POLICY.always_escalate) == {
        "bank_detail_change",
        "closed_period_effect",
        "missing_grant_approval",
        "forfeiture",
        "unconfirmed_person",
        "funding_mismatch",
    }


@pytest.mark.parametrize(
    ("amount", "outcome"),
    [
        ("1.50", ACT_ALONE),
        ("24.99", ACT_ALONE),
        ("25.00", ACT_ALONE),
        ("25.01", ESCALATE),
        ("400.00", ESCALATE),
        ("-30.00", ESCALATE),
        ("-1.50", ACT_ALONE),
    ],
)
def test_usd_differences_against_the_threshold(amount, outcome):
    decision = assess(POLICY, Decimal(amount), "USD")
    assert decision.outcome == outcome
    assert decision.measured == abs(Decimal(amount))
    assert "25.00 USD" in decision.reason


def test_other_currencies_are_measured_in_usd():
    # 20 GBP is 25.50 USD at the reference rate, so it crosses the line that 20 USD does not.
    assert assess(POLICY, Decimal("20.00"), "USD").outcome == ACT_ALONE
    crossed = assess(POLICY, Decimal("20.00"), "GBP", rate=GBP_USD)
    assert crossed.outcome == ESCALATE
    assert crossed.measured == Decimal("25.50")
    assert assess(POLICY, Decimal("19.60"), "GBP", rate=GBP_USD).outcome == ACT_ALONE


def test_without_a_rate_vestly_does_not_guess():
    decision = assess(POLICY, Decimal("1.00"), "EUR", rate=None)
    assert decision.outcome == ESCALATE
    assert "reference rate" in decision.reason


@pytest.mark.parametrize("kind", POLICY.always_escalate)
def test_some_differences_always_go_to_a_person(kind):
    decision = assess(POLICY, Decimal("0.01"), "USD", kind=kind)
    assert decision.outcome == ESCALATE
    assert "any amount" in decision.reason


def test_disagreeing_sources_always_go_to_a_person():
    assert assess(POLICY, Decimal("0.01"), "USD", sources_agree=False).outcome == ESCALATE


def test_an_ordinary_kind_follows_the_amount():
    assert assess(POLICY, Decimal("3.00"), "USD", kind="bank_fee").outcome == ACT_ALONE
    assert assess(POLICY, Decimal("300.00"), "USD", kind="bank_fee").outcome == ESCALATE


def test_rates_come_from_the_rate_card():
    card = {"gbp_usd_reference": "1.2750"}
    assert rate_to(card, "USD") == Decimal("1")
    assert rate_to(card, "GBP") == GBP_USD
    assert rate_to(card, "EUR") is None
