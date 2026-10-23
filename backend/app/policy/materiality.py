"""Materiality: how big a difference must be before a person has to look at it.

The threshold lives in the policy file. This module only applies it, so the same
rule answers the reconciler, the drafts, the scorer, and a reviewer asking why.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.ledger.money import CENT, money_str
from app.policy.loader import Policy

ACT_ALONE = "act_alone"
ESCALATE = "escalate"


@dataclass(frozen=True)
class MaterialityDecision:
    outcome: str
    reason: str
    amount: Decimal
    currency: str
    measured: Decimal | None
    threshold: Decimal
    threshold_currency: str


def rate_to(rate_card: dict, currency: str, target: str = "USD") -> Decimal | None:
    """The reference rate from a period's rate card, for example gbp_usd_reference."""
    if currency == target:
        return Decimal("1")
    value = rate_card.get(f"{currency.lower()}_{target.lower()}_reference")
    return Decimal(value) if value is not None else None


def assess(
    policy: Policy,
    amount: Decimal,
    currency: str,
    rate: Decimal | None = None,
    kind: str | None = None,
    sources_agree: bool = True,
) -> MaterialityDecision:
    """Decide whether Vestly may act alone on a difference, or must escalate.

    amount is the size of the difference in its own currency; the sign is ignored.
    rate converts one unit of that currency into the threshold currency.
    kind names the type of difference, so the always-escalate list can apply.
    """
    threshold = policy.materiality_absolute
    target = policy.materiality_currency
    size = abs(amount)

    def decide(outcome: str, reason: str, measured: Decimal | None) -> MaterialityDecision:
        return MaterialityDecision(outcome, reason, size, currency, measured, threshold, target)

    if kind is not None and kind in policy.always_escalate:
        return decide(ESCALATE, f"A {kind.replace('_', ' ')} goes to a person at any amount.", None)
    if not sources_agree:
        return decide(ESCALATE, "The trusted sources disagree, so a person decides.", None)
    if currency == target:
        measured = size
    elif rate is None:
        return decide(ESCALATE, f"No {currency} to {target} reference rate, so the size cannot be measured.", None)
    else:
        measured = (size * rate).quantize(CENT)
    limit = f"{money_str(threshold)} {target}"
    if measured > threshold:
        return decide(ESCALATE, f"{money_str(measured)} {target} is above the {limit} threshold.", measured)
    return decide(ACT_ALONE, f"{money_str(measured)} {target} is within the {limit} threshold.", measured)
