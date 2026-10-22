from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import init_db
from app.ledger.errors import LedgerError
from app.ledger.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Vestly", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(LedgerError)
def handle_ledger_error(_request: Request, exc: LedgerError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"error": exc.code, "message": exc.message})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "vestly-ledger"}
