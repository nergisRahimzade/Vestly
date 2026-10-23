"""Errors that say where and why.

Wrap a step in `doing("what is being done", id=value)`. If anything inside fails for a reason
nobody planned (a missing field, a broken file, a network error), the error is re-raised as a
ContextError that keeps the original as its cause and adds what was being done, with which
ids. Steps nest, so the message reads outside-in: "while loading the world (period=2026-03):
while storing the payroll section (row=4): KeyError: 'gross_wages'".

A LedgerError is not wrapped: it is a deliberate refusal with its own rule and message.
Nothing here swallows an error; every path re-raises or is recorded by `record_error`.
"""

import json
import logging
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.ledger.errors import LedgerError

LOG = logging.getLogger("vestly")
APP_ROOT = Path(__file__).resolve().parent


class ContextError(Exception):
    def __init__(self, doing: str, ids: dict, cause: BaseException) -> None:
        self.doing = doing
        self.ids = ids
        self.cause = cause
        super().__init__(self.describe())

    def describe(self) -> str:
        ids = ", ".join(f"{key}={value}" for key, value in self.ids.items())
        head = f"while {self.doing}" + (f" ({ids})" if ids else "")
        if isinstance(self.cause, ContextError):
            return f"{head}: {self.cause.describe()}"
        return f"{head}: {type(self.cause).__name__}: {self.cause}"

    def root(self) -> BaseException:
        cause = self.cause
        while isinstance(cause, ContextError):
            cause = cause.cause
        return cause

    def steps(self) -> list[dict]:
        found, current = [], self
        while isinstance(current, ContextError):
            found.append({"doing": current.doing, "ids": {key: str(value) for key, value in current.ids.items()}})
            current = current.cause
        return found


@contextmanager
def doing(what: str, **ids):
    try:
        yield
    except LedgerError:
        raise
    except Exception as exc:
        raise ContextError(what, ids, exc) from exc


def where(exc: BaseException) -> str:
    """The deepest line of Vestly's own code in the traceback: "app/world/loader.py:171 in store_drop"."""
    root = exc.root() if isinstance(exc, ContextError) else exc
    frames = traceback.extract_tb(root.__traceback__) or traceback.extract_tb(exc.__traceback__)
    ours = [frame for frame in frames if Path(frame.filename).resolve().is_relative_to(APP_ROOT)]
    frame = (ours or frames or [None])[-1]
    if frame is None:
        return "unknown"
    path = Path(frame.filename).resolve()
    shown = path.relative_to(APP_ROOT.parent).as_posix() if path.is_relative_to(APP_ROOT.parent) else path.name
    return f"{shown}:{frame.lineno} in {frame.name}"


def describe(exc: BaseException) -> str:
    return exc.describe() if isinstance(exc, ContextError) else f"{type(exc).__name__}: {exc}"


def record_error(exc: BaseException, *, request: str | None = None, actor: str | None = None, session=None) -> str:
    """Write an unexpected error to the error log and the application log. Returns its error id."""
    from app.record.models import ErrorLog

    error_id = f"err_{uuid.uuid4().hex[:12]}"
    root = exc.root() if isinstance(exc, ContextError) else exc
    row = ErrorLog(
        id=error_id,
        at=datetime.now(timezone.utc).isoformat(),
        where=where(exc),
        error_type=type(root).__name__,
        message=describe(exc)[:2000],
        steps_json=json.dumps(exc.steps() if isinstance(exc, ContextError) else []),
        request=request,
        actor=actor,
        traceback="".join(traceback.format_exception(type(root), root, root.__traceback__))[-8000:],
    )
    LOG.error("%s at %s: %s", error_id, row.where, row.message)
    try:
        if session is not None:
            session.rollback()
            session.add(row)
            session.commit()
        else:
            from app.db import SessionLocal

            with SessionLocal() as own:
                own.add(row)
                own.commit()
    except Exception:  # the error itself must still reach the caller; the log line above has it
        LOG.exception("%s could not be written to the error log", error_id)
    return error_id
