"""Fase 4a: cobranças Pix/boleto (sandbox), webhooks autenticados/idempotentes, divergências, RBAC e RLS."""

import json
import threading
import time
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"
D = Decimal


def _today():
    from app.core.clock import business_today

    return business_today()


@pytest.fixture()
def env(client):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial Cobranças"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "P"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 3, "area_m2": "300", "price": "120000.00"},
    ).json()
    customer = client.post(
        f"{API}/customers", headers=h, json={"name": "Pagador Pix", "document": "52998224725"}
    ).json()

    def contract(lot_idx: int = 0):
        res = client.post(
            f"{API}/reservations",
            headers=h,
            json={"lot_id": lots[lot_idx]["id"], "customer_id": customer["id"]},
        ).json()
        c = client.post(
            f"{API}/contracts",
            headers=h,
            json={
                "reservation_id": res["id"],
                "down_payment": "20000.00",
                "installments_count": 12,
                "interest_monthly_pct": "1",
                "first_due_date": (_today() + timedelta(days=30)).isoformat(),
            },
        ).json()
        r = client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1})
        assert r.status_code == 200, r.text
        insts = client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json()
        return c, insts

    return {"h": h, "contract": contract}


def _issue(client, h, inst_id, **body):
    return client.post(f"{API}/installments/{inst_id}/charges", headers=h, json=body)


def _simulate(client, h, charge_id, **body):
    return client.post(f"{API}/charges/{charge_id}/sandbox/simulate", headers=h, json=body)


def _signed(external_id, *, amount=None, paid_on=None, event_id=None, etype="charge.paid"):
    from app.services.psp.sandbox import SandboxProvider

    return SandboxProvider().build_event(
        etype=etype, external_id=external_id, amount=amount, paid_on=paid_on, event_id=event_id
    )


def _hook(client, tenant_id, body, headers):
    return client.post(
        f"{API}/webhooks/payments/sandbox/{tenant_id}",
        content=body,
        headers={**headers, "Content-Type": "application/json"},
    )


def _inst(client, h, inst_id):
    return client.get(f"{API}/installments/{inst_id}", headers=h).json()


# ─────────────────────────── emissão ───────────────────────────


def test_issue_pix_and_boleto_charges(client, env):
    h = env["h"]
    _, insts = env["contract"]()
    entrada, p1 = insts[0], insts[1]
    r = _issue(client, h, entrada["id"], method="pix")
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "pendente" and c["provider"] == "sandbox" and c["method"] == "pix"
    assert c["amount"] == "20000.00" and c["due_date"] == _today().isoformat()
    assert c["pix_code"].startswith("SANDBOX-PIX-NAO-PAGAVEL") and c["boleto_line"] is None
    assert c["installment_number"] == 0 and c["customer_name"] == "Pagador Pix" and c["events"] == []

    # uma pendente por parcela
    r2 = _issue(client, h, entrada["id"], method="boleto")
    assert r2.status_code == 409 and "pendente" in r2.json()["detail"]

    b = _issue(client, h, p1["id"], method="boleto").json()
    assert b["due_date"] == p1["due_date"] and b["amount"] == p1["amount"]
    assert (
        b["boleto_line"].startswith("9990") and len(b["boleto_line"].replace(".", "").replace(" ", "")) == 47
    )

    listed = client.get(f"{API}/charges", headers=h, params={"installment_id": p1["id"]}).json()
    assert [x["id"] for x in listed] == [b["id"]]
    s = client.get(f"{API}/charges/summary", headers=h).json()
    assert s["pending_count"] == 2 and D(s["pending_total"]) == D("20000.00") + D(p1["amount"])
    assert [p["name"] for p in client.get(f"{API}/payment-providers", headers=h).json()] == ["sandbox"]


def test_issue_validations(client, env):
    h = env["h"]
    _, insts = env["contract"]()
    past = (_today() - timedelta(days=1)).isoformat()
    assert "passado" in _issue(client, h, insts[1]["id"], due_date=past).json()["detail"]
    far = (_today() + timedelta(days=400)).isoformat()
    assert "365" in _issue(client, h, insts[1]["id"], due_date=far).json()["detail"]
    assert _issue(client, h, str(uuid.uuid4())).status_code == 404
    # parcela quitada não recebe cobrança
    client.post(f"{API}/installments/{insts[0]['id']}/payments", headers=h, json={"amount": "20000.00"})
    r = _issue(client, h, insts[0]["id"])
    assert r.status_code == 409 and "aberto" in r.json()["detail"]


# ─────────────────────────── liquidação por webhook ───────────────────────────


def test_simulated_payment_settles_installment(client, env, world):
    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, h, insts[0]["id"]).json()
    r = _simulate(client, h, c["id"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["result"] == "processado"
    ch = out["charge"]
    assert ch["status"] == "paga" and ch["paid_amount"] == "20000.00" and ch["payment_id"]
    assert [(e["event_type"], e["result"]) for e in ch["events"]] == [("charge.paid", "processado")]

    inst = _inst(client, h, insts[0]["id"])
    assert inst["status"] == "paga"
    [p] = inst["payments"]
    assert p["id"] == ch["payment_id"] and p["method"] == "pix" and p["created_by_name"] is None
    assert c["external_id"] in p["notes"]
    s = client.get(f"{API}/charges/summary", headers=h).json()
    assert s["pending_count"] == 0 and s["paid_month_count"] == 1 and s["paid_month_total"] == "20000.00"
    # auditado como ação do sistema
    audit = client.get(f"{API}/audit-events", headers=h).json()
    assert any(a["action"] == "charge.paid" for a in audit)


def test_external_webhook_is_authenticated_and_deduplicated(client, env, world):
    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, h, insts[0]["id"]).json()
    body, headers = _signed(c["external_id"], amount=D("20000.00"), paid_on=_today(), event_id="evt-1")

    r = _hook(client, world.tenant_a, body, headers)
    assert r.status_code == 200 and r.json() == {"status": "processado"}
    # mesma entrega de novo (PSP reenviou): sem efeito
    r = _hook(client, world.tenant_a, body, headers)
    assert r.status_code == 200 and r.json() == {"status": "duplicado"}
    # outro evento de pagamento para a mesma cobrança: idempotente
    body2, headers2 = _signed(c["external_id"], amount=D("20000.00"), event_id="evt-2")
    assert _hook(client, world.tenant_a, body2, headers2).json() == {"status": "ignorado"}

    inst = _inst(client, h, insts[0]["id"])
    assert len(inst["payments"]) == 1 and inst["status"] == "paga"
    events = client.get(f"{API}/charges/{c['id']}", headers=h).json()["events"]
    assert sorted((e["event_id"], e["result"]) for e in events) == [
        ("evt-1", "processado"),
        ("evt-2", "ignorado"),
    ]


def test_webhook_rejects_bad_signatures(client, env, world):
    from app.services.psp.sandbox import sign

    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, h, insts[0]["id"]).json()
    body, headers = _signed(c["external_id"], amount=D("20000.00"))

    assert _hook(client, world.tenant_a, body, {}).status_code == 401  # sem assinatura
    tampered = body.replace(b"20000.00", b"1.00")
    assert _hook(client, world.tenant_a, tampered, headers).status_code == 401  # corpo alterado
    old = {"X-Sandbox-Signature": sign(body, int(time.time()) - 3600)}
    assert _hook(client, world.tenant_a, body, old).status_code == 401  # replay fora da janela
    assert _hook(client, world.tenant_a, body, {"X-Sandbox-Signature": "lixo"}).status_code == 401
    forged = {"X-Sandbox-Signature": f"t={int(time.time())},v1={'0' * 64}"}
    assert _hook(client, world.tenant_a, body, forged).status_code == 401
    # provedor e empresa desconhecidos
    assert client.post(f"{API}/webhooks/payments/outro/{world.tenant_a}", content=body).status_code == 404
    assert _hook(client, uuid.uuid4(), body, headers).status_code == 404
    # malformado, mas assinado
    junk = b'{"nada": 1}'
    assert _hook(client, world.tenant_a, junk, {"X-Sandbox-Signature": sign(junk)}).status_code == 400

    assert _inst(client, h, insts[0]["id"])["payments"] == []  # nada foi lançado
    assert client.get(f"{API}/charges/{c['id']}", headers=h).json()["status"] == "pendente"


def test_webhook_cannot_cross_tenants(client, env, world):
    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, h, insts[0]["id"]).json()
    body, headers = _signed(c["external_id"], amount=D("20000.00"))
    # URL da empresa B com cobrança da empresa A: não encontra (RLS), não lança nada
    r = _hook(client, world.tenant_b, body, headers)
    assert r.status_code == 200 and r.json() == {"status": "ignorado"}
    assert _inst(client, h, insts[0]["id"])["payments"] == []

    hb = login(client, "bob", "empresa-b")
    assert client.get(f"{API}/charges", headers=hb).json() == []
    assert client.get(f"{API}/charges/{c['id']}", headers=hb).status_code == 404
    assert _simulate(client, hb, c["id"]).status_code == 404
    assert (
        client.post(f"{API}/charges/{c['id']}/cancel", headers=hb, json={"reason": "xxx"}).status_code == 404
    )
    assert _issue(client, hb, insts[1]["id"]).status_code == 404


def test_concurrent_duplicate_deliveries_settle_once(client, env, world):
    from app.core.db import get_sessionmaker, set_tenant
    from app.services import charges as svc
    from app.services.psp.sandbox import SandboxProvider

    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, h, insts[0]["id"]).json()
    body, _ = _signed(c["external_id"], amount=D("20000.00"), event_id="evt-dup")
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def deliver() -> None:
        db = get_sessionmaker()()
        try:
            set_tenant(db, world.tenant_a)
            barrier.wait()
            out = svc.process_webhook(db, tenant_id=world.tenant_a, provider=SandboxProvider(), body=body)
            time.sleep(0.3)
            db.commit()
        finally:
            db.close()
        with lock:
            results.append(out.result)

    threads = [threading.Thread(target=deliver) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["duplicado", "processado"]
    assert len(_inst(client, h, insts[0]["id"])["payments"]) == 1


def test_late_payment_applies_late_charges(client, env, owner_engine):
    h = env["h"]
    _, insts = env["contract"]()
    p1 = insts[1]
    c = _issue(client, h, p1["id"]).json()
    with owner_engine.begin() as conn:  # parcela e cobrança venceram há 30 dias
        past = _today() - timedelta(days=30)
        conn.execute(text("UPDATE installments SET due_date = :d WHERE id = :i"), {"d": past, "i": p1["id"]})
        conn.execute(text("UPDATE charges SET due_date = :d WHERE id = :i"), {"d": past, "i": c["id"]})
    out = _simulate(client, h, c["id"]).json()
    assert out["result"] == "processado"
    [p] = _inst(client, h, p1["id"])["payments"]
    assert D(p["late_fee"]) > 0 and D(p["late_interest"]) > 0 and p["principal"] == p1["amount"]
    assert out["charge"]["paid_amount"] == p["amount"] and D(p["amount"]) > D(c["amount"])


def test_issue_on_overdue_installment_includes_charges(client, env, owner_engine):
    h = env["h"]
    _, insts = env["contract"]()
    p1 = insts[1]
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE installments SET due_date = :d WHERE id = :i"),
            {"d": _today() - timedelta(days=10), "i": p1["id"]},
        )
    c = _issue(client, h, p1["id"]).json()
    assert c["due_date"] == _today().isoformat() and D(c["amount"]) > D(p1["amount"])


# ─────────────────────────── expiração, cancelamento e divergência ───────────────────────────


def test_expired_and_cancelled_charges(client, env):
    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, h, insts[0]["id"]).json()
    out = _simulate(client, h, c["id"], event="expired").json()
    assert out["result"] == "processado" and out["charge"]["status"] == "expirada"
    assert _inst(client, h, insts[0]["id"])["status"] == "aberta"

    c2 = _issue(client, h, insts[0]["id"], method="boleto").json()  # nova cobrança liberada
    r = client.post(
        f"{API}/charges/{c2['id']}/cancel", headers=h, json={"reason": "cliente vai pagar em dinheiro"}
    )
    assert r.status_code == 200 and r.json()["status"] == "cancelada"
    assert r.json()["status_reason"] == "cliente vai pagar em dinheiro"
    again = client.post(f"{API}/charges/{c2['id']}/cancel", headers=h, json={"reason": "de novo"})
    assert again.status_code == 409


def test_paid_after_manual_payment_becomes_divergent(client, env):
    fabi = login(client, "fabi")
    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, fabi, insts[0]["id"]).json()
    # recebimento manual cancela a cobrança pendente
    client.post(f"{API}/installments/{insts[0]['id']}/payments", headers=fabi, json={"amount": "20000.00"})
    ch = client.get(f"{API}/charges/{c['id']}", headers=h).json()
    assert ch["status"] == "cancelada" and "manualmente" in ch["status_reason"]

    # ...mas o cliente pagou o Pix mesmo assim: dinheiro não some, vira divergência
    out = _simulate(client, fabi, c["id"]).json()
    assert out["result"] == "divergente" and out["charge"]["status"] == "divergente"
    assert out["charge"]["paid_amount"] == "20000.00" and "não aplicado" in out["charge"]["status_reason"]
    assert len(_inst(client, h, insts[0]["id"])["payments"]) == 1
    s = client.get(f"{API}/charges/summary", headers=h).json()
    assert s["divergent_count"] == 1 and s["divergent_total"] == "20000.00"
    assert [
        x["id"] for x in client.get(f"{API}/charges", headers=h, params={"status": "divergente"}).json()
    ] == [c["id"]]

    r = client.post(f"{API}/charges/{c['id']}/resolve", headers=fabi, json={"reason": "devolvido via Pix"})
    assert r.status_code == 200 and r.json()["status"] == "resolvida"
    assert "devolvido via Pix" in r.json()["status_reason"]
    assert (
        client.post(f"{API}/charges/{c['id']}/resolve", headers=fabi, json={"reason": "xxx"}).status_code
        == 409
    )


def test_overpayment_becomes_divergent(client, env):
    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, h, insts[0]["id"]).json()
    out = _simulate(client, h, c["id"], amount="25000.00").json()
    assert out["result"] == "divergente"
    assert _inst(client, h, insts[0]["id"])["payments"] == []


def test_renegotiation_and_rescission_cancel_pending_charges(client, env):
    h = env["h"]
    c1, insts = env["contract"](0)
    ch = _issue(client, h, insts[1]["id"]).json()
    r = client.post(
        f"{API}/contracts/{c1['id']}/renegotiations",
        headers=h,
        json={
            "installments_count": 20,
            "first_due_date": (_today() + timedelta(days=15)).isoformat(),
            "reason": "cliente pediu prazo",
        },
    )
    assert r.status_code == 201, r.text
    got = client.get(f"{API}/charges/{ch['id']}", headers=h).json()
    assert got["status"] == "cancelada" and "renegociada" in got["status_reason"]

    c2, insts2 = env["contract"](1)
    ch2 = _issue(client, h, insts2[2]["id"], method="boleto").json()
    r = client.post(
        f"{API}/contracts/{c2['id']}/rescind",
        headers=h,
        json={"reason": "desistência", "refund_due_date": (_today() + timedelta(days=30)).isoformat()},
    )
    assert r.status_code == 200, r.text
    got = client.get(f"{API}/charges/{ch2['id']}", headers=h).json()
    assert got["status"] == "cancelada" and "distrato" in got["status_reason"]


# ─────────────────────────── RBAC, append-only e configuração ───────────────────────────


def test_rbac(client, env):
    _, insts = env["contract"]()
    for user in ("dave", "carol", "gil"):  # comercial, leitura, corretor
        hh = login(client, user)
        assert client.get(f"{API}/charges", headers=hh).status_code == 403
        assert _issue(client, hh, insts[0]["id"]).status_code == 403
    fabi = login(client, "fabi")
    c = _issue(client, fabi, insts[0]["id"])
    assert c.status_code == 201
    dave = login(client, "dave")
    assert _simulate(client, dave, c.json()["id"]).status_code == 403
    assert (
        client.post(
            f"{API}/charges/{c.json()['id']}/resolve", headers=dave, json={"reason": "xxx"}
        ).status_code
        == 403
    )


def test_webhook_events_are_append_only(client, env, world, app_engine):
    h = env["h"]
    _, insts = env["contract"]()
    c = _issue(client, h, insts[0]["id"]).json()
    _simulate(client, h, c["id"])
    for sql in (
        "UPDATE webhook_events SET payload = '{}'",
        "UPDATE webhook_events SET event_id = 'x'",
        "DELETE FROM webhook_events",
        "DELETE FROM charges",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
            conn.execute(text(sql))
    # sem contexto de tenant, nada aparece (RLS)
    with app_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM webhook_events")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM charges")).scalar() == 0


def test_sandbox_disabled_in_production(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings().sandbox_enabled is False  # type: ignore[call-arg]
    monkeypatch.setenv("PAYMENTS_SANDBOX_ENABLED", "true")
    assert Settings().sandbox_enabled is True  # type: ignore[call-arg]
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("PAYMENTS_SANDBOX_ENABLED")
    assert Settings().sandbox_enabled is True  # type: ignore[call-arg]
    monkeypatch.setenv("PAYMENTS_SANDBOX_SECRET", "CHANGE_ME_0123456789abcdefghijklmnopqrstuvwxyz")
    with pytest.raises(ValueError):
        Settings()  # type: ignore[call-arg]


def test_sandbox_payload_is_obviously_fake():
    from app.services.psp.base import ChargeRequest
    from app.services.psp.sandbox import SandboxProvider

    p = SandboxProvider()
    req = ChargeRequest("ref", "pix", D("10.00"), _today(), "Fulano", None, "teste")
    issued = p.create_charge(req)
    assert "NAO-PAGAVEL" in (issued.pix_code or "") and issued.external_id.startswith("sbx_")
    body, headers = p.build_event(etype="charge.paid", external_id=issued.external_id, amount=D("10.00"))
    p.verify_webhook(headers, body)
    ev = p.parse_webhook(body)
    assert ev.amount == D("10.00") and ev.external_id == issued.external_id
    assert json.loads(body)["type"] == "charge.paid"
