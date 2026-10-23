"""What each role may do. Code, not data, so no route can grant a permission."""

from app.auth.identities import Actor
from app.ledger.errors import LedgerError

LEDGER_READ = "ledger.read"
PERIOD_OPEN = "period.open"
PERIOD_LOCK = "period.lock"
JOURNAL_DRAFT = "journal.draft"
JOURNAL_REVIEW = "journal.review"
JOURNAL_POST = "journal.post"
WORLD_LOAD = "world.load"
SOURCES_AGGREGATE = "sources.read_aggregate"
SOURCES_INDIVIDUAL = "sources.read_individual"
RECONCILE_RUN = "reconcile.run"
RECONCILE_READ = "reconcile.read"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "controller": frozenset(
        {
            LEDGER_READ,
            PERIOD_OPEN,
            PERIOD_LOCK,
            JOURNAL_REVIEW,
            JOURNAL_POST,
            WORLD_LOAD,
            SOURCES_AGGREGATE,
            SOURCES_INDIVIDUAL,
            RECONCILE_RUN,
            RECONCILE_READ,
        }
    ),
    # Vestly reads totals. One person's pay is visible to it only inside an open case.
    "agent": frozenset({LEDGER_READ, JOURNAL_DRAFT, SOURCES_AGGREGATE, RECONCILE_RUN, RECONCILE_READ}),
    "auditor": frozenset({LEDGER_READ, SOURCES_AGGREGATE, SOURCES_INDIVIDUAL, RECONCILE_READ}),
    "hr_ops": frozenset(),
    "hr_comp": frozenset(),
    "treasury": frozenset(),
    "legal": frozenset(),
    "payroll_provider": frozenset(),
    "dept_head": frozenset(),
    "none": frozenset(),
}


def allowed(actor: Actor, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(actor.role, frozenset())


def require(actor: Actor, permission: str, action: str) -> None:
    if not allowed(actor, permission):
        raise LedgerError("forbidden", f"{actor.handle} ({actor.role}) cannot {action}.", 403)
