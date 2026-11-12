import logging

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1 import (
    auth,
    commercial,
    contract_changes,
    contracts,
    crm,
    developments,
    finance,
    payables,
    reports,
    tenant,
)
from app.core.config import get_settings
from app.core.db import get_engine

VERSION = "0.8.0"
log = logging.getLogger("imobil.api")

settings = get_settings()
app = FastAPI(
    title="SysOrdem Imobil API",
    version=VERSION,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness: o processo responde. Não toca no banco."""
    return {"status": "ok", "service": "sysordem-imobil-api"}


@app.get("/ready", tags=["health"])
def ready() -> dict[str, str]:
    """Readiness: banco acessível e migrations aplicadas."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            encoding = conn.execute(text("SHOW server_encoding")).scalar()
    except SQLAlchemyError:
        log.warning("readiness: banco indisponível")
        raise HTTPException(status_code=503, detail="database unavailable") from None
    if not revision:
        raise HTTPException(status_code=503, detail="migrations pending")
    if encoding != "UTF8":
        # JSONB e acentos exigem UTF-8 (o driver decodifica JSON como UTF-8).
        raise HTTPException(status_code=503, detail=f"database encoding must be UTF8 (got {encoding})")
    return {"status": "ready", "database": "reachable", "revision": str(revision)}


v1 = APIRouter(prefix="/api/v1")


@v1.get("", tags=["meta"])
def api_root() -> dict[str, str]:
    return {"name": "SysOrdem Imobil API", "version": VERSION, "phase": "3c"}


v1.include_router(auth.router)
v1.include_router(tenant.router)
v1.include_router(developments.router)
v1.include_router(commercial.router)
v1.include_router(contracts.router)
v1.include_router(crm.router)
v1.include_router(finance.router)
v1.include_router(payables.router)
v1.include_router(contract_changes.router)
v1.include_router(reports.router)
app.include_router(v1)
