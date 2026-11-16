"""PSP real (Asaas): conta da empresa, cobranças, webhook confirmado na API, consulta e assinatura SaaS.

O Asaas é imitado por um httpx.MockTransport (`FakeAsaas`) que segue a API v3 documentada; o teste
contra o sandbox real fica em scripts/asaas_smoke.py.
"""

import base64
import json
import os
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"
KEY_A = "$aact_test_empresa_a_000000000000aaaa"
KEY_PLATFORM = "$aact_test_plataforma_0000000000zzzz"
BILLING_TOKEN = "token-da-plataforma-" + "x" * 30


def _today():
    from app.core.clock import business_today

    return business_today()


class FakeAsaas:
    def __init__(self) -> None:
        self.accounts = {KEY_A: "Empresa A Ltda", KEY_PLATFORM: "SysOrdem"}
        self.customers: dict[str, dict[str, Any]] = {}
        self.payments: dict[str, dict[str, Any]] = {}
        self.webhooks: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, str | None]] = []
        self.fail_qr = False
        self.down = False

    def pay(self, pid: str, value: str | None = None, day=None) -> None:
        p = self.payments[pid]
        p["status"] = "RECEIVED"
        if value is not None:
            p["value"] = float(value)
        p["clientPaymentDate"] = (day or _today()).isoformat()

    def handler(self, req: httpx.Request) -> httpx.Response:
        key = req.headers.get("access_token")
        path = req.url.path.removeprefix("/v3")
        self.calls.append((req.method, path, key))
        if self.down:
            raise httpx.ConnectError("fora do ar")
        assert req.headers.get("user-agent", "").startswith("SysOrdemImobil")
        if key not in self.accounts:
            return httpx.Response(401, json={"errors": [{"code": "invalid_access_token"}]})
        body = json.loads(req.content) if req.content else {}
        m = req.method
        if m == "GET" and path == "/myAccount/commercialInfo":
            return httpx.Response(200, json={"companyName": self.accounts[key]})
        if m == "GET" and path == "/customers":
            ref = req.url.params.get("externalReference")
            data = [c for c in self.customers.values() if c["externalReference"] == ref and c["key"] == key]
            return httpx.Response(200, json={"data": data, "totalCount": len(data)})
        if m == "POST" and path == "/customers":
            cid = f"cus_{len(self.customers) + 1:06d}"
            self.customers[cid] = {**body, "id": cid, "key": key}
            return httpx.Response(200, json=self.customers[cid])
        if m == "POST" and path == "/payments":
            if body["customer"] not in self.customers:
                return httpx.Response(400, json={"errors": [{"description": "Cliente inexistente"}]})
            pid = f"pay_{uuid.uuid4().hex[:12]}"
            self.payments[pid] = {
                **body,
                "id": pid,
                "status": "PENDING",
                "deleted": False,
                "invoiceUrl": f"https://sandbox.asaas.com/i/{pid}",
            }
            return httpx.Response(200, json=self.payments[pid])
        mp = re.fullmatch(r"/payments/(pay_\w+)(/pixQrCode|/identificationField)?", path)
        if mp:
            p = self.payments.get(mp.group(1))
            if p is None:
                return httpx.Response(404)
            if mp.group(2) == "/pixQrCode":
                if self.fail_qr:
                    return httpx.Response(400, json={"errors": [{"description": "QR indisponível"}]})
                return httpx.Response(200, json={"payload": f"00020126PIXREAL{p['id']}", "encodedImage": "x"})
            if mp.group(2) == "/identificationField":
                return httpx.Response(200, json={"identificationField": "2" * 47, "nossoNumero": "1"})
            if m == "DELETE":
                if p["status"] != "PENDING":
                    return httpx.Response(400, json={"errors": [{"description": "Cobrança já recebida"}]})
                p["deleted"] = True
                return httpx.Response(200, json={"deleted": True, "id": p["id"]})
            return httpx.Response(200, json=p)
        if m == "POST" and path == "/webhooks":
            wid = f"wh_{len(self.webhooks) + 1}"
            self.webhooks[wid] = {**body, "id": wid, "key": key}
            return httpx.Response(200, json={"id": wid})
        mw = re.fullmatch(r"/webhooks/(wh_\d+)", path)
        if m == "DELETE" and mw:
            self.webhooks.pop(mw.group(1), None)
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(404)


@pytest.fixture()
def fake(monkeypatch):
    from app.core import credentials
    from app.core.config import get_settings
    from app.services.psp import asaas

    f = FakeAsaas()
    monkeypatch.setattr(asaas, "TRANSPORT_OVERRIDE", httpx.MockTransport(f.handler))
    s = get_settings()
    monkeypatch.setattr(s, "payments_credentials_key", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(s, "public_api_url", "https://api.exemplo.com.br")
    credentials._aead.cache_clear()
    yield f
    credentials._aead.cache_clear()


@pytest.fixture()
def env(client, world):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial Asaas"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "A"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 3, "area_m2": "300", "price": "120000.00"},
    ).json()

    def contract(lot_idx: int = 0, document: str | None = "52998224725"):
        cust = client.post(
            f"{API}/customers", headers=h, json={"name": f"Comprador {lot_idx}", "document": document}
        ).json()
        res = client.post(
            f"{API}/reservations", headers=h, json={"lot_id": lots[lot_idx]["id"], "customer_id": cust["id"]}
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
        signed = client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1})
        assert signed.status_code == 200, signed.text
        return client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json()

    return {"h": h, "contract": contract}


def _connect(client, h, key=KEY_A, environment="sandbox"):
    return client.put(
        f"{API}/settings/payments", headers=h, json={"api_key": key, "environment": environment}
    )


def _hook(client, tenant_id, payload, token):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["asaas-access-token"] = token
    return client.post(
        f"{API}/webhooks/payments/asaas/{tenant_id}", content=json.dumps(payload), headers=headers
    )


def _event(pid, event="PAYMENT_RECEIVED", evt=None, value=999999.0, ref=None):
    return {
        "id": evt or f"evt_{uuid.uuid4().hex}&1",
        "event": event,
        "dateCreated": "2026-11-16 10:00:00",
        "payment": {"object": "payment", "id": pid, "value": value, "externalReference": ref},
    }


# ─────────────────────────── unidade ───────────────────────────


def test_credentials_are_bound_to_tenant_and_purpose(fake):
    from app.core import credentials

    t1, t2 = uuid.uuid4(), uuid.uuid4()
    sealed = credentials.seal(KEY_A, tenant_id=t1, purpose="psp-api-key")
    assert KEY_A not in sealed and sealed.startswith("v1:")
    assert credentials.open_sealed(sealed, tenant_id=t1, purpose="psp-api-key") == KEY_A
    with pytest.raises(credentials.CredentialsUnavailableError):
        credentials.open_sealed(sealed, tenant_id=t2, purpose="psp-api-key")  # outra empresa
    with pytest.raises(credentials.CredentialsUnavailableError):
        credentials.open_sealed(sealed, tenant_id=t1, purpose="outro-uso")


def test_webhook_token_and_payload_validation():
    from app.services.psp.asaas import AsaasClient, AsaasProvider, token_hash
    from app.services.psp.base import InvalidSignatureError

    p = AsaasProvider(AsaasClient(KEY_A, "sandbox"), webhook_token_sha256=token_hash("t" * 40))
    p.verify_webhook({"asaas-access-token": "t" * 40}, b"{}")
    for headers in ({}, {"asaas-access-token": "u" * 40}):
        with pytest.raises(InvalidSignatureError):
            p.verify_webhook(headers, b"{}")
    no_token = AsaasProvider(AsaasClient(KEY_A, "sandbox"), webhook_token_sha256=None)
    with pytest.raises(InvalidSignatureError):
        no_token.verify_webhook({"asaas-access-token": "t" * 40}, b"{}")
    for bad in (
        b"nao-json",
        b'{"event":"PAYMENT_RECEIVED"}',
        json.dumps({"id": "x" * 101, "event": "E"}).encode(),
    ):
        with pytest.raises(InvalidSignatureError):
            p.parse_webhook(bad)


# ─────────────────────────── conta da empresa ───────────────────────────


def test_connect_account_registers_webhook_and_seals_key(client, env, fake, owner_engine, world):
    h = env["h"]
    before = client.get(f"{API}/settings/payments", headers=h).json()
    assert before["configured"] is False and before["credentials_available"] is True

    bad = _connect(client, h, key="$aact_chave_que_nao_existe_000000")
    assert bad.status_code == 422 and "não aceitou" in bad.json()["detail"]

    r = _connect(client, h)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["configured"] and s["webhook"] and s["environment"] == "sandbox"
    assert s["account_name"] == "Empresa A Ltda" and s["api_key_hint"] == "…aaaa"
    assert KEY_A not in r.text
    (wh,) = fake.webhooks.values()
    assert wh["url"] == f"https://api.exemplo.com.br/api/v1/webhooks/payments/asaas/{world.tenant_a}"
    assert len(wh["authToken"]) >= 32 and wh["email"] == "alice@example.com" and wh["key"] == KEY_A
    with owner_engine.begin() as c:
        row = c.execute(text("SELECT api_key_sealed, webhook_token_sha256 FROM payment_accounts")).one()
        audit = c.execute(
            text("SELECT data::text FROM audit_events WHERE action='payment_account.connect'")
        ).scalar()
    assert KEY_A not in row[0] and wh["authToken"] not in (row[1] or "")
    assert KEY_A not in audit and wh["authToken"] not in audit

    provs = client.get(f"{API}/payment-providers", headers=h).json()
    assert provs[0] == {"name": "asaas", "label": "Asaas (sandbox)", "sandbox": True}

    # trocar a chave refaz o webhook (o antigo é removido no Asaas)
    assert _connect(client, h).status_code == 200
    assert len(fake.webhooks) == 1 and "wh_1" not in fake.webhooks


def test_account_settings_rbac_and_guards(client, env, fake, monkeypatch):
    from app.core import credentials
    from app.core.config import get_settings

    for user in ("carol", "fabi", "gil"):
        assert client.get(f"{API}/settings/payments", headers=login(client, user)).status_code == 403
    assert _connect(client, login(client, "erin")).status_code == 200  # admin pode
    # produção só aceita a conta real do Asaas
    monkeypatch.setattr(get_settings(), "environment", "production")
    r = _connect(client, env["h"], environment="sandbox")
    assert r.status_code == 422 and "produção" in r.json()["detail"]
    monkeypatch.setattr(get_settings(), "environment", "test")
    # servidor sem a chave do cofre não liga a conta
    monkeypatch.setattr(get_settings(), "payments_credentials_key", None)
    credentials._aead.cache_clear()
    r = _connect(client, env["h"])
    assert r.status_code == 422 and "PAYMENTS_CREDENTIALS_KEY" in r.json()["detail"]


def test_without_public_url_connects_without_webhook(client, env, fake, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "public_api_url", None)
    s = _connect(client, env["h"]).json()
    assert s["configured"] and s["webhook"] is False and fake.webhooks == {}


# ─────────────────────────── cobranças ───────────────────────────


def test_charge_lifecycle_with_webhook_confirmed_in_api(client, env, fake, world):
    h = env["h"]
    _connect(client, h)
    token = next(iter(fake.webhooks.values()))["authToken"]
    insts = env["contract"]()
    p1 = insts[1]
    r = client.post(f"{API}/installments/{p1['id']}/charges", headers=h, json={"method": "pix"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["provider"] == "asaas" and c["provider_label"] == "Asaas" and c["status"] == "pendente"
    assert c["pix_code"].startswith("00020126PIXREAL") and c["payment_url"].startswith(
        "https://sandbox.asaas"
    )
    pay = fake.payments[c["external_id"]]
    assert pay["billingType"] == "PIX" and pay["value"] == float(p1["amount"])
    assert pay["dueDate"] == p1["due_date"] and pay["externalReference"] == c["id"]
    assert pay["fine"] == {"value": 2.0, "type": "PERCENTAGE"} and pay["interest"] == {"value": 1.0}
    (cust,) = fake.customers.values()
    assert cust["cpfCnpj"] == "52998224725" and cust["notificationDisabled"] is True

    # outra parcela do mesmo comprador reaproveita o cadastro no Asaas
    b = client.post(
        f"{API}/installments/{insts[2]['id']}/charges", headers=h, json={"method": "boleto"}
    ).json()
    assert b["boleto_line"] == "2" * 47 and len(fake.customers) == 1

    pid = c["external_id"]
    assert _hook(client, world.tenant_a, _event(pid), None).status_code == 401
    assert _hook(client, world.tenant_a, _event(pid), "errado" * 8).status_code == 401
    # token certo, mas o Asaas ainda diz PENDENTE: webhook forjado/adiantado não paga nada
    r = _hook(client, world.tenant_a, _event(pid), token)
    assert r.status_code == 200 and r.json() == {"status": "ignorado"}
    assert client.get(f"{API}/installments/{p1['id']}", headers=h).json()["status"] == "aberta"

    fake.pay(pid)
    evt = _event(pid, evt="evt_pago&1")
    assert _hook(client, world.tenant_a, evt, token).json() == {"status": "processado"}
    assert _hook(client, world.tenant_a, evt, token).json() == {"status": "duplicado"}
    got = client.get(f"{API}/charges/{c['id']}", headers=h).json()
    assert got["status"] == "paga" and got["paid_amount"] == p1["amount"]  # valor da API, não do corpo
    assert client.get(f"{API}/installments/{p1['id']}", headers=h).json()["status"] == "paga"

    # Asaas fora do ar na confirmação: 503 para ele reenviar depois
    fake.down = True
    r = _hook(client, world.tenant_a, _event(b["external_id"]), token)
    assert r.status_code == 503
    fake.down = False

    # token da empresa A na URL da empresa B: B não tem conta Asaas
    assert _hook(client, world.tenant_b, _event(pid), token).status_code == 404


def test_sync_charge_applies_payment_and_deletion(client, env, fake):
    h = env["h"]
    _connect(client, h)
    insts = env["contract"]()
    a = client.post(f"{API}/installments/{insts[1]['id']}/charges", headers=h, json={"method": "pix"}).json()
    b = client.post(
        f"{API}/installments/{insts[2]['id']}/charges", headers=h, json={"method": "boleto"}
    ).json()
    r = client.post(f"{API}/charges/{a['id']}/sync", headers=h).json()
    assert r["result"] == "sem_alteracao" and r["charge"]["status"] == "pendente"
    fake.pay(a["external_id"])
    r = client.post(f"{API}/charges/{a['id']}/sync", headers=h).json()
    assert r["result"] == "processado" and r["charge"]["status"] == "paga"
    assert client.post(f"{API}/charges/{a['id']}/sync", headers=h).json()["result"] == "ignorado"
    fake.payments[b["external_id"]]["deleted"] = True  # removida no painel do Asaas
    r = client.post(f"{API}/charges/{b['id']}/sync", headers=h).json()
    assert r["result"] == "processado" and r["charge"]["status"] == "cancelada"
    assert client.post(f"{API}/charges/{a['id']}/sync", headers=login(client, "carol")).status_code == 403


def test_issue_errors_leave_nothing_behind(client, env, fake, owner_engine):
    h = env["h"]
    _connect(client, h)
    no_doc = env["contract"](0, document=None)
    r = client.post(f"{API}/installments/{no_doc[1]['id']}/charges", headers=h, json={"method": "pix"})
    assert r.status_code == 409 and "CPF ou CNPJ" in r.json()["detail"]
    ok = env["contract"](1)
    fake.fail_qr = True
    r = client.post(f"{API}/installments/{ok[1]['id']}/charges", headers=h, json={"method": "pix"})
    assert r.status_code == 409 and "QR" in r.json()["detail"]
    (p,) = fake.payments.values()
    assert p["deleted"] is True  # apagada no Asaas
    with owner_engine.begin() as c:
        assert c.execute(text("SELECT count(*) FROM charges")).scalar() == 0


def test_overdue_installment_charge_has_no_extra_fees_and_cancel_hits_asaas(client, env, fake, owner_engine):
    h = env["h"]
    _connect(client, h)
    insts = env["contract"]()
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE installments SET due_date = :d WHERE id = :i"),
            {"d": _today() - timedelta(days=10), "i": insts[1]["id"]},
        )
    c1 = client.post(f"{API}/installments/{insts[1]['id']}/charges", headers=h, json={"method": "pix"}).json()
    pay = fake.payments[c1["external_id"]]
    assert Decimal(c1["amount"]) > Decimal(insts[1]["amount"])  # já com multa/mora do dia
    assert pay["fine"]["value"] == 0 and pay["interest"]["value"] == 0
    r = client.post(f"{API}/charges/{c1['id']}/cancel", headers=h, json={"reason": "emitida errada"})
    assert r.status_code == 200 and r.json()["status"] == "cancelada" and pay["deleted"] is True


def test_disconnect_requires_no_pending_charges(client, env, fake):
    h = env["h"]
    _connect(client, h)
    insts = env["contract"]()
    c = client.post(f"{API}/installments/{insts[1]['id']}/charges", headers=h, json={"method": "pix"}).json()
    assert client.get(f"{API}/settings/payments", headers=h).json()["pending_charges"] == 1
    r = client.post(f"{API}/settings/payments/disconnect", headers=h, json={"reason": "trocar de banco"})
    assert r.status_code == 409 and "pendente" in r.json()["detail"]
    client.post(f"{API}/charges/{c['id']}/cancel", headers=h, json={"reason": "desligar conta"})
    r = client.post(f"{API}/settings/payments/disconnect", headers=h, json={"reason": "trocar de banco"})
    assert r.status_code == 200 and r.json()["configured"] is False and fake.webhooks == {}
    assert client.get(f"{API}/payment-providers", headers=h).json()[0]["name"] == "sandbox"


def test_payment_accounts_rls(app_engine, client, env, fake, world):
    _connect(client, env["h"])
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_b)})
        assert c.execute(text("SELECT count(*) FROM payment_accounts")).scalar() == 0
    with app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
        assert c.execute(text("SELECT count(*) FROM payment_accounts")).scalar() == 1


# ─────────────────────────── assinatura SaaS (conta da plataforma) ───────────────────────────


@pytest.fixture()
def platform(fake, monkeypatch):
    from app.api.v1.billing import reset_signup_limit
    from app.core.config import get_settings

    reset_signup_limit()
    s = get_settings()
    monkeypatch.setattr(s, "billing_asaas_api_key", KEY_PLATFORM)
    monkeypatch.setattr(s, "billing_asaas_webhook_token", BILLING_TOKEN)
    monkeypatch.setattr(s, "billing_asaas_environment", "sandbox")
    return fake


def test_subscription_invoice_paid_through_platform_asaas(client, world, platform, owner_engine):
    r = client.post(
        f"{API}/signup",
        json={
            "company_name": "Loteadora Nova",
            "slug": "loteadora-nova",
            "full_name": "Dona da Nova",
            "email": "dona@nova.example.com",
            "password": "senha-da-empresa-nova",
            "plan_code": "essencial",
            "accept_terms": True,
        },
    )
    assert r.status_code == 201, r.text
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    with owner_engine.begin() as c:
        tid = c.execute(text("SELECT id FROM tenants WHERE slug='loteadora-nova'")).scalar()
        c.execute(
            text("UPDATE tenant_subscriptions SET trial_ends_at = :d WHERE tenant_id = :t"),
            {"d": _today() + timedelta(days=3), "t": tid},
        )
    inv = client.get(f"{API}/billing", headers=h).json()["invoices"][0]
    r = client.post(f"{API}/billing/invoices/{inv['id']}/pay", headers=h, json={"method": "pix"})
    assert r.status_code == 409 and "CNPJ" in r.json()["detail"]
    bad = client.put(f"{API}/settings/company-document", headers=h, json={"document": "123"})
    assert bad.status_code == 422
    r = client.put(f"{API}/settings/company-document", headers=h, json={"document": "11.222.333/0001-81"})
    assert r.status_code == 200 and r.json()["company_document"] == "11222333000181"

    paid = client.post(f"{API}/billing/invoices/{inv['id']}/pay", headers=h, json={"method": "pix"}).json()
    assert paid["pix_code"].startswith("00020126PIXREAL") and paid["payment_url"]
    (pay,) = platform.payments.values()
    assert pay["externalReference"] == f"{tid}:{inv['id']}" and pay["fine"]["value"] == 0
    assert all(k == KEY_PLATFORM for m, path, k in platform.calls if path.startswith("/payments"))
    (cust,) = platform.customers.values()
    assert cust["cpfCnpj"] == "11222333000181" and cust["externalReference"] == f"empresa:{tid}"

    url = f"{API}/webhooks/billing/asaas"
    evt = _event(pay["id"], evt="evt_assinatura&1", ref=pay["externalReference"])
    hdr = {"Content-Type": "application/json"}
    assert client.post(url, content=json.dumps(evt), headers=hdr).status_code == 401
    ok_h = {**hdr, "asaas-access-token": BILLING_TOKEN}
    assert client.post(url, content=json.dumps(evt), headers=ok_h).json() == {
        "status": "ignorado"
    }  # pendente
    platform.pay(pay["id"])
    evt2 = _event(pay["id"], evt="evt_assinatura&2", ref="forjada:qualquer")
    assert client.post(url, content=json.dumps(evt2), headers=ok_h).json() == {"status": "pago"}
    assert client.post(url, content=json.dumps(evt2), headers=ok_h).json() == {"status": "duplicado"}
    b = client.get(f"{API}/billing", headers=h).json()
    assert b["status"]["state"] == "ativa" and b["invoices"][0]["status"] == "paga"


def test_production_guards_for_asaas(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "p" * 64)
    monkeypatch.setenv("CORS_ORIGINS", '["https://app.exemplo.com.br"]')
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://imobil_app:forte@db:5432/imobil")
    monkeypatch.setenv("BILLING_ASAAS_API_KEY", KEY_PLATFORM)
    monkeypatch.setenv("BILLING_ASAAS_WEBHOOK_TOKEN", BILLING_TOKEN)
    monkeypatch.setenv("BILLING_ASAAS_ENVIRONMENT", "sandbox")
    with pytest.raises(ValueError, match="BILLING_ASAAS_ENVIRONMENT"):
        Settings()  # type: ignore[call-arg]
    monkeypatch.setenv("BILLING_ASAAS_ENVIRONMENT", "production")
    monkeypatch.setenv("PUBLIC_API_URL", "http://api.exemplo.com.br")
    with pytest.raises(ValueError, match="PUBLIC_API_URL"):
        Settings()  # type: ignore[call-arg]
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.exemplo.com.br/")
    s = Settings()  # type: ignore[call-arg]
    assert s.public_api_url == "https://api.exemplo.com.br" and s.billing_asaas_configured


# ─────────────────────────── estorno automático ───────────────────────────


def test_refund_confirmed_in_api_reverses_payment(client, env, fake, world):
    h = env["h"]
    _connect(client, h)
    token = next(iter(fake.webhooks.values()))["authToken"]
    assert "PAYMENT_PARTIALLY_REFUNDED" in next(iter(fake.webhooks.values()))["events"]
    insts = env["contract"]()
    p1 = insts[1]
    c = client.post(f"{API}/installments/{p1['id']}/charges", headers=h, json={"method": "pix"}).json()
    pid = c["external_id"]
    fake.pay(pid)
    assert _hook(client, world.tenant_a, _event(pid, evt="evt_pago&1"), token).json() == {
        "status": "processado"
    }

    # aviso de estorno, mas o Asaas ainda não devolveu (refund PENDING): nada muda
    fake.payments[pid]["refunds"] = [{"status": "PENDING", "value": float(p1["amount"])}]
    r = _hook(client, world.tenant_a, _event(pid, event="PAYMENT_REFUNDED", evt="evt_ref&1"), token)
    assert r.json() == {"status": "ignorado"}
    assert client.get(f"{API}/charges/{c['id']}", headers=h).json()["status"] == "paga"

    # parcial devolvido de fato (DONE): sinaliza, não estorna sozinho
    fake.payments[pid]["refunds"] = [{"status": "DONE", "value": 100.0}]
    r = _hook(client, world.tenant_a, _event(pid, event="PAYMENT_PARTIALLY_REFUNDED", evt="evt_par&1"), token)
    assert r.json() == {"status": "divergente"}
    got = client.get(f"{API}/charges/{c['id']}", headers=h).json()
    assert got["status"] == "paga" and "Estorno parcial de R$ 100.00" in got["status_reason"]

    # estorno total (status REFUNDED) chega pelo botão "Atualizar situação" (webhook perdido)
    fake.payments[pid]["status"] = "REFUNDED"
    fake.payments[pid]["refunds"] = [
        {"status": "DONE", "value": 100.0},
        {"status": "DONE", "value": float(p1["amount"]) - 100.0},
    ]
    out = client.post(f"{API}/charges/{c['id']}/sync", headers=h).json()
    assert out["result"] == "processado" and out["charge"]["status"] == "estornada"
    inst = client.get(f"{API}/installments/{p1['id']}", headers=h).json()
    assert inst["status"] == "aberta" and inst["payments"][0]["reversed"]
    assert client.post(f"{API}/charges/{c['id']}/sync", headers=h).json()["result"] == "ignorado"


def test_asaas_refund_mapping_counts_only_done_refunds():
    from app.services.psp.asaas import AsaasClient, AsaasProvider
    from app.services.psp.base import EVENT_PAID, EVENT_PARTIALLY_REFUNDED, EVENT_REFUNDED

    p = AsaasProvider(AsaasClient(KEY_A, "sandbox"), webhook_token_sha256=None)
    base = {"id": "pay_x", "value": 500.0, "status": "RECEIVED", "clientPaymentDate": "2026-11-10"}
    assert p._event_from(base, event_id="e", raw={}).type == EVENT_PAID
    pending = {**base, "refunds": [{"status": "PENDING", "value": 500.0}]}
    assert p._event_from(pending, event_id="e", raw={}).type == EVENT_PAID
    two = {**base, "refunds": [{"status": "DONE", "value": 120.5}, {"status": "DONE", "value": 79.5}]}
    ev = p._event_from(two, event_id="e", raw={})
    assert ev.type == EVENT_PARTIALLY_REFUNDED and ev.amount == Decimal("200.00")
    full = {**base, "status": "REFUNDED"}
    ev = p._event_from(full, event_id="e", raw={})
    assert ev.type == EVENT_REFUNDED and ev.amount == Decimal("500.00")
