import hmac
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1 import (
    auth,
    billing,
    charges,
    commercial,
    contract_changes,
    contracts,
    crm,
    developments,
    esign,
    finance,
    mfa,
    password,
    payables,
    payment_accounts,
    portal,
    reconciliation,
    reports,
    site,
    tenant,
)
from app.core import metrics
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.http import RateLimiter, SecurityMiddleware, configure_logging

VERSION = "0.16.0"
log = logging.getLogger("imobil.api")

settings = get_settings()
if settings.environment != "test":
    configure_logging(settings.effective_log_format, settings.log_level)
webhook_limiter = (
    RateLimiter(settings.webhook_rate_per_minute) if settings.webhook_rate_per_minute > 0 else None
)
app = FastAPI(
    title="SysOrdem Imobil API",
    version=VERSION,
    docs_url=None if settings.is_production else "/docs",
    # em produção o esquema da API também não fica público (mapa de rotas para quem ataca)
    openapi_url=None if settings.is_production else "/openapi.json",
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
# Fica por fora do CORS: vale também para respostas de erro e para os 413/429 que ele mesmo gera.
app.add_middleware(
    SecurityMiddleware,
    production=settings.is_production,
    max_body_bytes=settings.max_body_bytes,
    webhook_limiter=webhook_limiter,
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


@lru_cache
def _migration_head() -> str | None:
    """Revisão mais nova das migrations que vêm NESTA imagem (para comparar com a do banco)."""
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        ini = Path(__file__).resolve().parents[1] / "alembic.ini"
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(ini.parent / "migrations"))
        cfg.set_main_option("path_separator", "os")
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001 — sem migrations na imagem: só não compara
        log.warning("ops: não foi possível ler a revisão das migrations")
        return None


@app.get("/ops/status", include_in_schema=False)
def ops_status(response: Response, x_ops_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Saúde para o monitor do servidor (deploy/monitor.sh). Fora do nginx e com token.

    200 = tudo bem; 503 = degradado (a lista `problems` diz o quê). Sem OPS_TOKEN configurado, ou com
    token errado, responde 404 (não revela que a rota existe).
    """
    token = get_settings().ops_token
    if not token or not x_ops_token or not hmac.compare_digest(token, x_ops_token):
        raise HTTPException(status_code=404, detail="Not Found")
    s = get_settings()
    problems: list[str] = []
    revision = None
    db_ms = None
    try:
        t0 = time.perf_counter()
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        db_ms = round((time.perf_counter() - t0) * 1000, 1)
    except SQLAlchemyError:
        problems.append("banco indisponível")
    head = _migration_head()
    if revision is not None and head is not None and revision != head:
        problems.append(f"migrations do banco ({revision}) diferentes das do código ({head})")
    if db_ms is not None and db_ms > s.ops_db_slow_ms:
        problems.append(f"banco lento ({db_ms} ms)")
    snap = metrics.snapshot()
    if snap.errors_5xx >= s.ops_max_errors_15m:
        problems.append(f"{snap.errors_5xx} erros 5xx nos últimos 15 min")
    if problems:
        response.status_code = 503
    return {
        "status": "degradado" if problems else "ok",
        "problems": problems,
        "version": VERSION,
        "revision": revision,
        "migration_head": head,
        "db_ms": db_ms,
        "uptime_seconds": snap.uptime_seconds,
        "last_15m": {"requests": snap.requests, "errors_5xx": snap.errors_5xx, "slow": snap.slow},
    }


v1 = APIRouter(prefix="/api/v1")


@v1.get("", tags=["meta"])
def api_root() -> dict[str, str]:
    return {"name": "SysOrdem Imobil API", "version": VERSION, "phase": "6b"}


v1.include_router(auth.router)
v1.include_router(mfa.router)
v1.include_router(password.router)
v1.include_router(tenant.router)
v1.include_router(developments.router)
v1.include_router(commercial.router)
v1.include_router(contracts.router)
v1.include_router(crm.router)
v1.include_router(finance.router)
v1.include_router(payables.router)
v1.include_router(contract_changes.router)
v1.include_router(reports.router)
v1.include_router(charges.router)
v1.include_router(payment_accounts.router)
v1.include_router(reconciliation.router)
v1.include_router(site.router)
v1.include_router(site.public)
v1.include_router(portal.router)
v1.include_router(billing.router)
v1.include_router(esign.router)
app.include_router(v1)
