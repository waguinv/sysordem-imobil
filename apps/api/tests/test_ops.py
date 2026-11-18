"""Monitoramento: contadores de saúde e a rota interna /ops/status."""

import pytest

TOKEN = "token-de-ops-de-teste-0123456789"


def test_metrics_window_counts_and_expires():
    from app.core import metrics

    metrics.reset()
    t0 = 1000.0
    metrics.record(200, 10, now=t0)
    metrics.record(500, 10, now=t0 + 1)
    metrics.record(503, 2500, now=t0 + 2)  # erro e lento
    s = metrics.snapshot(now=t0 + 3)
    assert (s.requests, s.errors_5xx, s.slow) == (3, 2, 1)
    s = metrics.snapshot(now=t0 + metrics.WINDOW_SECONDS + 2)  # os dois primeiros saíram da janela
    assert (s.requests, s.errors_5xx, s.slow) == (1, 1, 1)
    metrics.reset()


@pytest.fixture()
def ops(monkeypatch):
    from app.core import metrics
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "ops_token", TOKEN)
    metrics.reset()
    yield
    metrics.reset()


@pytest.mark.db
def test_ops_status_requires_token(client, ops, monkeypatch):
    from app.core.config import get_settings

    assert client.get("/ops/status").status_code == 404
    assert client.get("/ops/status", headers={"X-Ops-Token": "errado"}).status_code == 404
    monkeypatch.setattr(get_settings(), "ops_token", None)  # sem token configurado, a rota não existe
    assert client.get("/ops/status", headers={"X-Ops-Token": TOKEN}).status_code == 404


@pytest.mark.db
def test_ops_status_ok_and_degraded(client, ops, monkeypatch):
    from app.core import metrics
    from app.core.config import get_settings

    client.get("/health")
    r = client.get("/ops/status", headers={"X-Ops-Token": TOKEN})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["problems"] == []
    assert body["revision"] == body["migration_head"] and body["revision"]  # banco = código
    assert body["db_ms"] is not None and body["last_15m"]["requests"] >= 1

    # erros 5xx demais nos últimos 15 min → 503 com o motivo
    for _ in range(get_settings().ops_max_errors_15m):
        metrics.record(500, 5)
    r = client.get("/ops/status", headers={"X-Ops-Token": TOKEN})
    assert r.status_code == 503 and "erros 5xx" in r.json()["problems"][0]

    # banco "lento" (limite artificialmente baixo)
    metrics.reset()
    monkeypatch.setattr(get_settings(), "ops_db_slow_ms", -1.0)
    r = client.get("/ops/status", headers={"X-Ops-Token": TOKEN})
    assert r.status_code == 503 and r.json()["problems"][0].startswith("banco lento")


@pytest.mark.db
def test_ops_status_flags_migration_mismatch(client, ops, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "_migration_head", lambda: "9999")
    r = client.get("/ops/status", headers={"X-Ops-Token": TOKEN})
    assert r.status_code == 503 and "migrations do banco" in r.json()["problems"][0]


def test_ops_token_placeholder_is_rejected(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("OPS_TOKEN", "CHANGE_ME_OPS_TOKEN_MIN_24_CHARS")
    with pytest.raises(ValueError, match="OPS_TOKEN"):
        Settings()  # type: ignore[call-arg]
    monkeypatch.setenv("OPS_TOKEN", "")
    assert Settings().ops_token is None  # type: ignore[call-arg]
