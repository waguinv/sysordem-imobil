"""Fase 5c: planos, cadastro self-service, limites, faturas, webhook, suspensão, cancelamento e isolamento."""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"
PW = "senha-da-empresa-nova"


def _today():
    from app.core.clock import business_today

    return business_today()


@pytest.fixture(autouse=True)
def _reset_signup_limit():
    from app.api.v1.billing import reset_signup_limit

    reset_signup_limit()


def _signup(client, slug="loteadora-nova", email="dona@nova.example.com", plan="essencial", **extra):
    body = {
        "company_name": "Loteadora Nova",
        "slug": slug,
        "full_name": "Dona da Nova",
        "email": email,
        "password": PW,
        "plan_code": plan,
        "accept_terms": True,
        **extra,
    }
    return client.post(f"{API}/signup", json=body)


@pytest.fixture()
def nova(client, world):
    r = _signup(client)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _set_trial_end(owner_engine, days_from_today: int, slug="loteadora-nova"):
    with owner_engine.begin() as c:
        c.execute(
            text(
                "UPDATE tenant_subscriptions SET trial_ends_at = :d "
                "WHERE tenant_id = (SELECT id FROM tenants WHERE slug = :s)"
            ),
            {"d": _today() + timedelta(days=days_from_today), "s": slug},
        )


def _billing(client, h):
    r = client.get(f"{API}/billing", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _dev(client, h, name="Empreendimento"):
    return client.post(f"{API}/developments", headers=h, json={"name": name})


# ─────────────────────────── planos e cadastro ───────────────────────────


def test_public_plans(client, world):
    plans = client.get(f"{API}/public/plans").json()
    assert [(p["code"], p["monthly_price"]) for p in plans] == [
        ("essencial", "197.00"),
        ("profissional", "397.00"),
        ("empresarial", "797.00"),
    ]
    assert plans[2]["max_lots"] is None


def test_signup_creates_company_owner_and_trial(client, nova):
    me = client.get(f"{API}/me", headers=nova).json()
    assert me["role"] == "owner" and me["tenant"]["slug"] == "loteadora-nova"
    b = _billing(client, nova)
    assert b["status"]["state"] == "teste" and b["status"]["plan_code"] == "essencial"
    assert b["status"]["trial_ends_at"] == (_today() + timedelta(days=14)).isoformat()
    assert b["invoices"] == [] and b["usage"] == {"developments": 0, "lots": 0, "users": 1}
    # login normal funciona com a senha escolhida
    r = client.post(
        f"{API}/auth/login",
        json={"tenant_slug": "loteadora-nova", "email": "dona@nova.example.com", "password": PW},
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"slug": "app"}, 422),
        ({"slug": "Com Espaço"}, 422),
        ({"slug": "-x"}, 422),
        ({"slug": "empresa-a"}, 409),
        ({"email": "alice@example.com"}, 409),
        ({"accept_terms": False}, 422),
        ({"website": "http://spam"}, 400),
        ({"password": "curta"}, 422),
        ({"plan": "inexistente"}, 422),
    ],
)
def test_signup_validation(client, world, over, code):
    kw = {k: v for k, v in over.items() if k in ("slug", "email", "plan")}
    extra = {k: v for k, v in over.items() if k not in kw}
    r = _signup(client, **kw, **extra)
    assert r.status_code == code, r.text


def test_signup_rate_limit(client, world):
    from app.api.v1 import billing

    for i in range(billing.SIGNUP_MAX_PER_HOUR):
        assert _signup(client, slug=f"empresa-{i:03d}", email=f"e{i}@x.example.com").status_code == 201
    assert _signup(client, slug="mais-uma", email="mais@x.example.com").status_code == 429


# ─────────────────────────── limites do plano ───────────────────────────


def test_plan_limits_developments_lots_and_users(client, nova):
    assert _dev(client, nova, "Primeiro").status_code == 201
    r = _dev(client, nova, "Segundo")
    assert r.status_code == 409 and "Essencial" in r.json()["detail"]
    dev = client.get(f"{API}/developments", headers=nova).json()[0]
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=nova, json={"code": "A"}).json()
    batch = {"block_id": block["id"], "first": 1, "last": 301, "area_m2": "250", "price": "1000"}
    assert (
        client.post(f"{API}/developments/{dev['id']}/lots/batch", headers=nova, json=batch).status_code == 409
    )
    batch["last"] = 300
    assert (
        client.post(f"{API}/developments/{dev['id']}/lots/batch", headers=nova, json=batch).status_code == 201
    )
    one = {"block_id": block["id"], "number": "999", "area_m2": "250", "price": "1000"}
    assert client.post(f"{API}/developments/{dev['id']}/lots", headers=nova, json=one).status_code == 409
    # usuários: proprietário + 2
    member = lambda n: {  # noqa: E731
        "email": f"m{n}@nova.example.com", "full_name": f"Membro {n}", "role": "comercial",
        "initial_password": "senha-inicial-123", "reason": "equipe",
    }  # fmt: skip
    ids = [client.post(f"{API}/members", headers=nova, json=member(n)) for n in (1, 2)]
    assert [r.status_code for r in ids] == [201, 201]
    assert client.post(f"{API}/members", headers=nova, json=member(3)).status_code == 409
    first = ids[0].json()["id"]
    client.patch(f"{API}/members/{first}", headers=nova, json={"is_active": False, "reason": "saiu"})
    assert client.post(f"{API}/members", headers=nova, json=member(3)).status_code == 201
    r = client.patch(f"{API}/members/{first}", headers=nova, json={"is_active": True, "reason": "voltou"})
    assert r.status_code == 409  # reativar também conta


def test_change_plan_up_and_down(client, nova):
    r = client.post(f"{API}/billing/plan", headers=nova, json={"plan_code": "profissional"})
    assert r.status_code == 200 and r.json()["status"]["plan_code"] == "profissional"
    assert _dev(client, nova, "Alfa").status_code == 201 and _dev(client, nova, "Beta").status_code == 201
    r = client.post(f"{API}/billing/plan", headers=nova, json={"plan_code": "essencial"})
    assert r.status_code == 409 and "2 empreendimentos" in r.json()["detail"]
    assert (
        client.post(f"{API}/billing/plan", headers=nova, json={"plan_code": "profissional"}).status_code
        == 409
    )


def test_company_without_subscription_has_no_limits(client, world):
    h = login(client, "alice")
    b = _billing(client, h)
    assert b["status"]["state"] == "sem_assinatura" and b["status"]["can_write"]
    for i in range(3):
        assert _dev(client, h, f"Sem limite {i}").status_code == 201
    assert client.post(f"{API}/billing/plan", headers=h, json={"plan_code": "essencial"}).status_code == 409


# ─────────────────────────── faturas e pagamento ───────────────────────────


def test_invoice_generated_before_trial_ends_and_paid_by_webhook(client, nova, owner_engine):
    _set_trial_end(owner_engine, 5)
    b = _billing(client, nova)
    assert len(b["invoices"]) == 1
    inv = b["invoices"][0]
    end = _today() + timedelta(days=5)
    assert (inv["period_start"], inv["due_date"], inv["amount"], inv["status"]) == (
        end.isoformat(), end.isoformat(), "197.00", "aberta",
    )  # fmt: skip
    assert len(_billing(client, nova)["invoices"]) == 1  # não duplica
    # trocar de plano antes de pagar ajusta o valor da fatura em aberto
    client.post(f"{API}/billing/plan", headers=nova, json={"plan_code": "profissional"})
    assert _billing(client, nova)["invoices"][0]["amount"] == "397.00"
    paid = client.post(f"{API}/billing/invoices/{inv['id']}/pay", headers=nova, json={"method": "pix"}).json()
    assert paid["pix_code"].startswith("SANDBOX-PIX")
    again = client.post(
        f"{API}/billing/invoices/{inv['id']}/pay", headers=nova, json={"method": "boleto"}
    ).json()
    assert again["pix_code"] == paid["pix_code"]  # a mesma cobrança
    r = client.post(f"{API}/billing/invoices/{inv['id']}/sandbox/simulate", headers=nova).json()
    assert r["result"] == "pago"
    from app.services.finance import add_months

    st = r["billing"]["status"]
    assert st["state"] == "ativa" and st["covered_until"] == add_months(end, 1).isoformat()
    assert r["billing"]["invoices"][0]["status"] == "paga" and r["billing"]["amount_due"] == "0.00"


def test_external_webhook_is_authenticated_idempotent_and_checks_amount(client, nova, owner_engine, world):
    from app.services.psp.sandbox import SandboxProvider, sign

    _set_trial_end(owner_engine, 3)
    inv = _billing(client, nova)["invoices"][0]
    ext = client.post(f"{API}/billing/invoices/{inv['id']}/pay", headers=nova, json={}).json()
    with owner_engine.begin() as c:
        tid = c.execute(text("SELECT id FROM tenants WHERE slug = 'loteadora-nova'")).scalar()
        external = c.execute(
            text("SELECT external_id FROM subscription_invoices WHERE id = :i"), {"i": inv["id"]}
        ).scalar()
    assert ext["status"] == "aberta"
    url = f"{API}/webhooks/billing/sandbox/{tid}"
    p = SandboxProvider()
    low, low_h = p.build_event(etype="charge.paid", external_id=external, amount=Decimal("10.00"))
    assert (
        client.post(url, content=low, headers={**low_h, "x-sandbox-signature": "t=1,v1=abc"}).status_code
        == 401
    )
    assert client.post(url, content=low, headers=low_h).json() == {"status": "valor_divergente"}
    assert _billing(client, nova)["invoices"][0]["status"] == "aberta"
    ok, ok_h = p.build_event(
        etype="charge.paid", external_id=external, amount=Decimal(inv["amount"]), event_id="evt_1"
    )
    assert client.post(url, content=ok, headers=ok_h).json() == {"status": "pago"}
    assert client.post(url, content=ok, headers={"x-sandbox-signature": sign(ok)}).json() == {
        "status": "duplicado"
    }
    assert _billing(client, nova)["status"]["state"] == "ativa"


# ─────────────────────────── atraso, suspensão e cancelamento ───────────────────────────


def test_overdue_then_suspended_blocks_writes_until_paid(client, nova, owner_engine):
    _set_trial_end(owner_engine, -3)
    b = _billing(client, nova)
    assert b["status"]["state"] == "em_atraso" and b["status"]["can_write"]
    assert _dev(client, nova).status_code == 201  # na carência tudo funciona
    _set_trial_end(owner_engine, -11)
    st = client.get(f"{API}/billing/status", headers=nova).json()
    assert st["state"] == "suspensa" and not st["can_write"]
    r = _dev(client, nova, "Bloqueado")
    assert r.status_code == 402 and "Assinatura suspensa" in r.json()["detail"]
    assert client.get(f"{API}/developments", headers=nova).status_code == 200  # leitura liberada
    inv = [i for i in _billing(client, nova)["invoices"] if i["status"] == "aberta"][0]
    assert client.post(f"{API}/billing/invoices/{inv['id']}/pay", headers=nova, json={}).status_code == 200
    r = client.post(f"{API}/billing/invoices/{inv['id']}/sandbox/simulate", headers=nova).json()
    assert r["billing"]["status"]["state"] == "ativa"
    assert _dev(client, nova, "Liberado").status_code == 409  # volta a gravar (aqui barra o limite do plano)


def test_cancel_keeps_access_until_end_then_read_only(client, nova, owner_engine):
    _set_trial_end(owner_engine, 5)
    assert len(_billing(client, nova)["invoices"]) == 1
    r = client.post(f"{API}/billing/cancel", headers=nova, json={"reason": "fechando a empresa"}).json()
    assert r["status"]["cancelled"] and r["status"]["state"] == "teste"
    assert {i["status"] for i in r["invoices"]} == {"cancelada"}
    _set_trial_end(owner_engine, -1)
    st = client.get(f"{API}/billing/status", headers=nova).json()
    assert st["state"] == "cancelada" and not st["can_write"]
    assert _dev(client, nova).status_code == 402
    r = client.post(f"{API}/billing/reactivate", headers=nova).json()
    assert r["status"]["state"] in ("em_atraso", "suspensa") and not r["status"]["cancelled"]
    assert any(i["status"] == "aberta" for i in r["invoices"])


# ─────────────────────────── primeiros passos ───────────────────────────


def test_onboarding_checklist(client, nova):
    o = client.get(f"{API}/onboarding", headers=nova).json()
    assert (o["done"], o["total"]) == (0, 6)
    _dev(client, nova)
    o = client.get(f"{API}/onboarding", headers=nova).json()
    assert [s["key"] for s in o["steps"] if s["done"]] == ["empreendimento"]


# ─────────────────────────── isolamento, RLS e RBAC ───────────────────────────


def test_isolation_and_rbac(client, nova, owner_engine, world):
    _set_trial_end(owner_engine, 2)
    inv = _billing(client, nova)["invoices"][0]
    alice = login(client, "alice")
    assert client.post(f"{API}/billing/invoices/{inv['id']}/pay", headers=alice, json={}).status_code == 404
    assert _billing(client, alice)["invoices"] == []
    erin = login(client, "erin")  # admin: vê, não altera a assinatura (hipótese)
    assert client.get(f"{API}/billing", headers=erin).status_code == 200
    assert client.post(f"{API}/billing/cancel", headers=erin, json={"reason": "abc"}).status_code == 403
    dave = login(client, "dave")  # comercial: não vê assinatura, mas vê o aviso de situação
    assert client.get(f"{API}/billing", headers=dave).status_code == 403
    assert client.get(f"{API}/billing/status", headers=dave).status_code == 200


def test_rls_and_grants(app_engine, client, nova, owner_engine):
    _set_trial_end(owner_engine, 2)
    _billing(client, nova)
    with app_engine.begin() as c:
        for table in ("tenant_subscriptions", "subscription_invoices", "billing_events"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608
        assert c.execute(text("SELECT count(*) FROM plans")).scalar() == 3  # catálogo global
    for sql in (
        "UPDATE tenants SET is_active = false",
        "INSERT INTO tenants (id, slug, name, is_active) VALUES (gen_random_uuid(), 'x-y-z', 'X', false)",
        "UPDATE plans SET monthly_price = 1",
        "DELETE FROM billing_events",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as c:
            c.execute(text(sql))
