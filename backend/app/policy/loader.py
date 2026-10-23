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
    always_escalate: tuple[str, ...]
    fx_band: Decimal
    min_group_size: int
    contributions: tuple[tuple[str, Decimal], ...]
    statutory: tuple[tuple[str, Decimal, Decimal], ...]
    approval_limit: Decimal
    approval_limit_currency: str
    callback_window_days: int
    reminder_after_days: int
    escalate_after_reminders: int
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

    def withholding_rate(self, entity_code: str) -> Decimal:
        return self._statutory(entity_code)[0]

    def employer_tax_rate(self, entity_code: str) -> Decimal:
        return self._statutory(entity_code)[1]

    def _statutory(self, entity_code: str) -> tuple[Decimal, Decimal]:
        for code, withholding, employer_tax in self.statutory:
            if code == entity_code:
                return withholding, employer_tax
        raise KeyError(entity_code)


_CACHE: dict[Path, tuple[tuple[int, int], Policy]] = {}


def load_policy(path: Path = POLICY_PATH) -> Policy:
    """The policy as the file says now. Parsed again whenever the file changes, so the hash is always current."""
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    policy = _parse_policy(path)
    _CACHE[path] = (stamp, policy)
    return policy


def _parse_policy(path: Path) -> Policy:
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
    statutory = tuple(
        (code, Decimal(rates["withholding"]), Decimal(rates["employer_tax"]))
        for code, rates in document["statutory_rates"].items()
    )
    controls = document["controls"]
    return Policy(
        version=document["version"],
        company=document["company"],
        materiality_absolute=Decimal(document["materiality"]["absolute"]),
        materiality_currency=document["materiality"]["currency"],
        always_escalate=tuple(document["materiality"]["always_escalate"]),
        fx_band=Decimal(document["fx"]["band"]),
        min_group_size=int(document["privacy"]["min_group_size"]),
        contributions=contributions,
        statutory=statutory,
        approval_limit=Decimal(controls["approval_limit"]),
        approval_limit_currency=controls["approval_limit_currency"],
        callback_window_days=int(controls["callback_window_days"]),
        reminder_after_days=int(controls["reminder_after_days"]),
        escalate_after_reminders=int(controls["escalate_after_reminders"]),
        facts=facts,
        content_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )
