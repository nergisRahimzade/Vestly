import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.agent.routes import router as agent_router
from app.cases.routes import router as cases_router
from app.controls.gate import log_refusal
from app.controls.routes import router as controls_router
from app.db import SessionLocal, init_db
from app.ledger.errors import LedgerError
from app.ledger.routes import router
from app.equity.routes import router as equity_router
from app.payroll.routes import router as payroll_router
from app.reconcile.routes import router as reconcile_router
from app.record.routes import router as record_router
from app.world.routes import router as world_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from app.cases.service import resume

    # Every case lives in the database; after a restart, pick each one up where it stood.
    with SessionLocal() as session:
        resume(session)
    if os.environ.get("VESTLY_SKIP_WORLD") != "1":
        from app.auth.identities import CONTROLLER_HANDLE, Actor
        from app.world.loader import load_world

        with SessionLocal() as session:
            load_world(session, Actor(handle=CONTROLLER_HANDLE, role="controller"))
    yield


app = FastAPI(title="Vestly", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(world_router)
app.include_router(reconcile_router)
app.include_router(payroll_router)
app.include_router(equity_router)
app.include_router(record_router)
app.include_router(controls_router)
app.include_router(cases_router)
app.include_router(agent_router)


@app.exception_handler(LedgerError)
def handle_ledger_error(_request: Request, exc: LedgerError) -> JSONResponse:
    """Every refusal names its rule and goes into the evidence log, so the record shows what was tried."""
    content = {"error": exc.code, "message": exc.message}
    if exc.rule is not None:
        content["rule"] = exc.rule
        log_refusal(exc)
    return JSONResponse(status_code=exc.status, content=content)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "vestly-ledger"}
