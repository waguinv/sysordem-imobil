"""Fase 6a: camada HTTP (cabeçalhos, id de requisição, limites) e travas de configuração de produção."""

import json
import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.http import JsonFormatter, RateLimiter, SecurityMiddleware, request_id_var
from app.main import app


def _mini(max_body: int = 100, limit: int = 2, production: bool = False) -> TestClient:
    mini = FastAPI()

    @mini.post("/api/v1/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @mini.post("/api/v1/webhooks/x")
    def hook() -> dict[str, str]:
        return {"ok": "1"}

    @mini.get("/api/v1/cached")
    def cached() -> dict[str, str]:
        return {"ok": "1"}

    mini.add_middleware(
        SecurityMiddleware, production=production, max_body_bytes=max_body, webhook_limiter=RateLimiter(limit)
    )
    return TestClient(mini)


def test_security_headers_on_real_app():
    c = TestClient(app)
    for path in ("/health", "/api/v1"):
        r = c.get(path)
        assert r.status_code == 200
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert len(r.headers["x-request-id"]) >= 8
    assert TestClient(app).get("/api/v1").headers["cache-control"] == "no-store"
    # fora de produção não manda HSTS (localhost em http)
    assert "strict-transport-security" not in c.get("/health").headers


def test_hsts_only_in_production():
    assert (
        _mini(production=True)
        .get("/api/v1/cached")
        .headers["strict-transport-security"]
        .startswith("max-age=")
    )


def test_request_id_echo_and_sanitize():
    c = TestClient(app)
    assert (
        c.get("/health", headers={"X-Request-ID": "abc12345-xyz"}).headers["x-request-id"] == "abc12345-xyz"
    )
    forged = c.get("/health", headers={"X-Request-ID": "x\r\nSet-Cookie: a=b"}).headers["x-request-id"]
    assert forged != "x\r\nSet-Cookie: a=b" and len(forged) == 32
    assert c.get("/health", headers={"X-Request-ID": "short"}).headers["x-request-id"] != "short"


def test_errors_also_get_headers():
    r = TestClient(app).get("/api/v1/contracts")  # sem token
    assert r.status_code == 401
    assert r.headers["x-content-type-options"] == "nosniff"


def test_body_limit_by_content_length():
    c = _mini(max_body=100)
    assert c.post("/api/v1/echo", content=b"a" * 100).json() == {"size": 100}
    r = c.post("/api/v1/echo", content=b"a" * 101)
    assert r.status_code == 413
    assert r.headers["x-request-id"]


def test_body_limit_streamed_without_content_length():
    c = _mini(max_body=100)

    def chunks():
        for _ in range(5):
            yield b"a" * 50

    r = c.post("/api/v1/echo", content=chunks())
    assert r.status_code == 413


def test_invalid_content_length():
    c = _mini()
    r = c.post("/api/v1/echo", content=b"x", headers={"content-length": "abc"})
    assert r.status_code == 400


def test_webhook_rate_limit():
    c = _mini(limit=2)
    assert c.post("/api/v1/webhooks/x").status_code == 200
    assert c.post("/api/v1/webhooks/x").status_code == 200
    r = c.post("/api/v1/webhooks/x")
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    # outras rotas não entram nesse limite
    assert c.post("/api/v1/echo", content=b"x").status_code == 200


def test_rate_limiter_window_and_prune():
    rl = RateLimiter(2, window_seconds=10, max_keys=3)
    assert rl.hit("a", now=0) is None
    assert rl.hit("a", now=1) is None
    assert rl.hit("a", now=2) == pytest.approx(8)
    assert rl.hit("a", now=10.5) is None  # a primeira saiu da janela
    for k in ("b", "c"):
        rl.hit(k, now=11)
    rl.hit("d", now=30)  # cheio: descarta chaves vencidas antes de crescer
    assert len(rl._hits) <= 3


def test_json_log_has_request_id_and_no_query():
    token = request_id_var.set("rid-123456")
    try:
        rec = logging.LogRecord("imobil.access", logging.INFO, __file__, 1, "request", None, None)
        rec.http = {"method": "GET", "path": "/api/v1/customers", "status": 200}
        out = json.loads(JsonFormatter().format(rec))
    finally:
        request_id_var.reset(token)
    assert out["request_id"] == "rid-123456"
    assert out["http"]["path"] == "/api/v1/customers"


PROD_OK = {
    "environment": "production",
    "jwt_secret": "p" * 64,
    "database_url": "postgresql+psycopg://imobil_app:S3nh4-forte-de-verdade@db:5432/imobil",
    "cors_origins": ["https://app.exemplo.com.br"],
}


def test_production_config_accepts_safe_values():
    s = Settings(**PROD_OK)
    assert s.is_production and not s.esign_sandbox and not s.billing_sandbox
    assert s.effective_log_format == "json"


@pytest.mark.parametrize(
    ("override", "msg"),
    [
        ({"database_url": "postgresql+psycopg://imobil_app:local_only@db/imobil"}, "senha de exemplo"),
        ({"cors_origins": ["http://localhost:3000"]}, "CORS_ORIGINS"),
        ({"payments_sandbox_enabled": True}, "PAYMENTS_SANDBOX_ENABLED"),
        ({"esign_sandbox_enabled": True}, "ESIGN_SANDBOX_ENABLED"),
        ({"jwt_secret": "x" * 40}, "48+"),
        ({"migration_database_url": PROD_OK["database_url"]}, "papel das migrations"),
    ],
)
def test_production_config_rejects_dev_values(override, msg):
    with pytest.raises(ValidationError, match=msg):
        Settings(**{**PROD_OK, **override})


def test_dev_config_unchanged():
    s = Settings(environment="development", jwt_secret="d" * 32)
    assert s.effective_log_format == "text" and s.sandbox_enabled


def test_openapi_hidden_in_production(monkeypatch):
    import importlib

    import app.core.config as config_mod
    import app.core.http as http_mod
    import app.main as main_mod

    monkeypatch.setattr(http_mod, "configure_logging", lambda *a, **k: None)  # não mexe no logging do pytest

    for k, v in {
        "ENVIRONMENT": "production",
        "JWT_SECRET": "p" * 64,
        "DATABASE_URL": "postgresql+psycopg://imobil_app:S3nh4-forte-de-verdade@db:5432/imobil",
        "CORS_ORIGINS": '["https://app.exemplo.com.br"]',
    }.items():
        monkeypatch.setenv(k, v)
    config_mod.get_settings.cache_clear()
    try:
        prod = importlib.reload(main_mod)
        c = TestClient(prod.app)
        assert c.get("/openapi.json").status_code == 404
        assert c.get("/docs").status_code == 404
        assert c.get("/health").headers["strict-transport-security"].startswith("max-age=")
    finally:
        monkeypatch.undo()
        config_mod.get_settings.cache_clear()
        importlib.reload(main_mod)
