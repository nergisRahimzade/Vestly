"""Start the demo over on a fresh database, optionally with scenarios a judge asks for.

    python -m app.world.reset [--world company|cast] [--scenario KIND:EMPLOYEE:PERIOD ...] [--db vestly.db]

Nothing is deleted: the old database file is moved to runs/ with a timestamp, so every earlier run can
still be opened and replayed. Then the world is loaded into a new file with each scenario planted in its
month's drop. Restart the server on the new file (the default path is the one the server uses).

Kinds: ghost_employee, bank_change_by_email, wrong_contribution, duplicate_payment, department_miscoded,
missing_from_register, vesting_typo, duplicate_grant. A ghost needs an employee number
HR does not know (e.g. NL-US-999); every other kind needs a real one.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def parse_scenario(text: str) -> dict:
    parts = text.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"A scenario is KIND:EMPLOYEE:PERIOD, e.g. ghost_employee:NL-US-999:2026-10; got {text!r}.")
    kind, employee_number, period = parts
    return {"kind": kind, "employee_number": employee_number, "period": period}


def reset(db_path: Path, world: str, scenarios: list[dict], runs: Path | None = None) -> dict:
    from sqlalchemy.orm import sessionmaker

    from app.auth.identities import CONTROLLER_HANDLE, Actor
    from app.db import build_engine, init_db
    from app.world.loader import load_world

    kept = None
    if db_path.exists():
        runs = runs or BACKEND / "runs"
        runs.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        kept = runs / f"{db_path.stem}-{stamp}{db_path.suffix}"
        shutil.move(str(db_path), kept)
    engine = build_engine(f"sqlite+pysqlite:///{db_path.as_posix()}")
    init_db(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        result = load_world(session, Actor(CONTROLLER_HANDLE, "controller"), world, scenarios)
    finally:
        session.close()
        engine.dispose()
    return {"database": str(db_path), "previous_kept_at": None if kept is None else str(kept), **result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset the demo database, keeping the old one, with optional scenarios.")
    parser.add_argument("--world", choices=("company", "cast"), default=os.environ.get("VESTLY_WORLD", "company"))
    parser.add_argument("--scenario", action="append", type=parse_scenario, default=[], help="KIND:EMPLOYEE:PERIOD (repeatable)")
    parser.add_argument("--db", type=Path, default=BACKEND / "vestly.db")
    args = parser.parse_args(argv)
    from app.ledger.errors import LedgerError

    try:
        result = reset(args.db.resolve(), args.world, args.scenario)
    except LedgerError as exc:
        print(f"Refused ({exc.code}): {exc.message}", file=sys.stderr)
        return 1
    print(f"New {result['world']} world in {result['database']} ({result['people']} people).")
    if result["previous_kept_at"]:
        print(f"The previous database was kept at {result['previous_kept_at']}.")
    for item in result.get("scenarios", []):
        print(f"Planted {item['kind']} on {item['employee_number']} in {item['period']}.")
    print("Start the server on this file: uvicorn app.main:app (from backend/).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
