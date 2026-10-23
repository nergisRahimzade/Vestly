import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

POLICY_PATH = Path(__file__).resolve().parent / "northlight.yaml"


@dataclass(frozen=True)
class RankedSource:
    source: str
    reason: str


@dataclass(frozen=True)
class FactPolicy:
    fact: str
    authority: str
    ranking: tuple[RankedSource, ...]
    email_is_source: bool | None = None


@dataclass(frozen=True)
class Policy:
    version: str
    company: str
    materiality_absolute: Decimal
    materiality_currency: str
    fx_band: Decimal
    contributions: tuple[tuple[str, Decimal], ...]
    facts: tuple[FactPolicy, ...]
    content_hash: str

    def fact(self, name: str) -> FactPolicy:
        for item in self.facts:
            if item.fact == name:
                return item
        raise KeyError(name)

    def contribution_rate(self, entity_code: str) -> Decimal:
        for code, rate in self.contributions:
            if code == entity_code:
                return rate
        raise KeyError(entity_code)


def load_policy(path: Path = POLICY_PATH) -> Policy:
    raw_text = path.read_text(encoding="utf-8")
    document = yaml.safe_load(raw_text)
    facts = tuple(
        FactPolicy(
            fact=name,
            authority=body["authority"],
            email_is_source=body.get("email_is_source"),
            ranking=tuple(RankedSource(source=item["source"], reason=item["reason"]) for item in body["ranking"]),
        )
        for name, body in document["facts"].items()
    )
    contributions = tuple((code, Decimal(rate)) for code, rate in document["contributions"].items())
    return Policy(
        version=document["version"],
        company=document["company"],
        materiality_absolute=Decimal(document["materiality"]["absolute"]),
        materiality_currency=document["materiality"]["currency"],
        fx_band=Decimal(document["fx"]["band"]),
        contributions=contributions,
        facts=facts,
        content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )
