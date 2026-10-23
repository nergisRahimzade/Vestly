"""The latest scored run, as the scorer wrote it. The server never scores on a request: it shows what a real run left."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends

from app.auth.deps import current_actor
from app.auth.identities import Actor
from app.auth.permissions import AUDIT_READ, require
from app.ledger.errors import LedgerError

router = APIRouter(prefix="/eval")

REPORTS = Path(__file__).resolve().parents[2] / "reports"


def report_path(world: str, period: str | None = None) -> Path:
    """The default close's report, or a held-out month's (score-company-2026-11.json)."""
    return REPORTS / (f"score-{world}-{period}.json" if period else f"score-{world}.json")


@router.get("/report")
def latest_report(world: str = "company", period: str | None = None, who: Actor = Depends(current_actor)) -> dict:
    """The report `python -m app.eval.score --world <world> --runs 3 --json reports/score-<world>.json` wrote, with when and how."""
    require(who, AUDIT_READ, "read the scorer report")
    if world not in ("company", "cast"):
        raise LedgerError("invalid_world", f"No world {world!r}.", 422)
    if period is not None and period not in ("2026-11", "2026-12"):
        raise LedgerError("invalid_period", f"Stored reports exist for the default close and the held-out months 2026-11, 2026-12; not {period}.", 422)
    path = report_path(world, period)
    if not path.exists():
        raise LedgerError(
            "no_report",
            f"No scored run recorded for {path.stem}. Run: python -m app.eval.score --world {world}"
            f"{f' --period {period}' if period else ''} --runs 3 --json reports/{path.name}",
            404,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LedgerError("bad_report", f"Could not read {path.name}: {exc}", 500) from exc
    return {key: payload.get(key) for key in ("generated_at", "command", "world", "exit_code", "report")}
