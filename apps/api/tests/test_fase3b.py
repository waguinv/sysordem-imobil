"""Fase 3b: comissões, repasses ao proprietário, contas a pagar — regras, estornos, isolamento e RBAC."""

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
    owner = client.post(f"{API}/land-owners", headers=h, json={"name": "Dono da Gleba"}).json()
    dev = client.post(
        f"{API}/developments", headers=h, json={"name": "Loteamento Repasse", "land_owner_id": owner["id"]}
    ).json()
    r = client.put(
        f"{API}/developments/{dev['id']}/owner-share",
        headers=h,
        json={"owner_share_pct": "10", "reason": "acordo"},
    )
    assert r.status_code == 200, r.text
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "D"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 3, "area_m2": "300", "price": "100000.00"},
    ).json()
    customer = client.post(f"{API}/customers", headers=h, json={"name": "Comprador Comissão"}).json()
    broker = client.post(
        f"{API}/brokers", headers=h, json={"name": "Corretora Quatro", "commission_pct": "4"}
    ).json()

    def contract(lot_idx: int = 0, broker_id: str | None = broker["id"], **terms):
        res = client.post(
            f"{API}/reservations",
            headers=h,
            json={"lot_id": lots[lot_idx]["id"], "customer_id": customer["id"], "broker_id": broker_id},
        ).json()
        body = {
            "reservation_id": res["id"],
            "down_payment": "10000.00",
            "installments_count": 10,
            "interest_monthly_pct": "0",
            "first_due_date": (_today() + timedelta(days=30)).isoformat(),
            **terms,
        }
        c = client.post(f"{API}/contracts", headers=h, json=body).json()
        r = client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1})
        assert r.status_code == 200, r.text
        inst = client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json()
        return c, inst

    return {"h": h, "owner": owner, "dev": dev, "broker": broker, "contract": contract}


def _pay(client, h, inst_id, amount):
    r = client.post(f"{API}/installments/{inst_id}/payments", headers=h, json={"amount": amount})
    assert r.status_code == 201, r.text
    return r.json()


def _balances(client, h, kind):
    return {
        b["beneficiary_name"]: b
        for b in client.get(f"{API}/participations/balances", headers=h, params={"kind": kind}).json()
    }


# ─────────────────────────── regras ───────────────────────────


def test_default_settings_and_update(client, env):
    h = env["h"]
    s = client.get(f"{API}/settings/finance", headers=h).json()
    assert s == {"commission_basis": "recebimento", "default_commission_pct": "5.00", "is_default": True}
    r = client.put(
        f"{API}/settings/finance",
        headers=h,
        json={"commission_basis": "assinatura", "default_commission_pct": "6", "reason": "nova política"},
    )
    assert r.json()["commission_basis"] == "assinatura" and r.json()["is_default"] is False
    fabi = login(client, "fabi")  # financeiro vê, não altera
    assert client.get(f"{API}/settings/finance", headers=fabi).status_code == 200
    assert (
        client.put(
            f"{API}/settings/finance",
            headers=fabi,
            json={"commission_basis": "recebimento", "default_commission_pct": "1", "reason": "abc"},
        ).status_code
        == 403
    )


def test_owner_share_requires_land_owner(client, env):
    h = env["h"]
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Sem Dono"}).json()
    r = client.put(
        f"{API}/developments/{dev['id']}/owner-share",
        headers=h,
        json={"owner_share_pct": "5", "reason": "x y"},
    )
    assert r.status_code == 422
    shares = {s["development_name"]: s for s in client.get(f"{API}/owner-shares", headers=h).json()}
    assert shares["Loteamento Repasse"]["owner_share_pct"] == "10.00"
    assert shares["Loteamento Repasse"]["land_owner_name"] == "Dono da Gleba"


# ─────────────────────────── créditos ───────────────────────────


def test_receipts_credit_commission_and_owner_share(client, env):
    h = env["h"]
    _, inst = env["contract"]()
    _pay(client, h, inst[0]["id"], "10000.00")  # entrada
    _pay(client, h, inst[1]["id"], "4500.00")  # parcial da 1ª (9.000,00)
    com = _balances(client, h, "comissao")["Corretora Quatro"]
    assert com["pending_total"] == "580.00" and com["pending_count"] == 2  # 4% de 14.500
    own = _balances(client, h, "proprietario")["Dono da Gleba"]
    assert own["pending_total"] == "1450.00"  # 10% de 14.500


def test_late_charges_are_not_commission_base(client, env, owner_engine):
    h = env["h"]
    _, inst = env["contract"]()
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE installments SET due_date = :d WHERE id = :i"),
            {"d": _today() - timedelta(days=30), "i": inst[1]["id"]},
        )
    total = client.get(f"{API}/installments/{inst[1]['id']}/quote", headers=h).json()["total"]
    assert D(total) > D("9000.00")
    _pay(client, h, inst[1]["id"], total)
    assert _balances(client, h, "comissao")["Corretora Quatro"]["pending_total"] == "360.00"  # 4% de 9.000


def test_signature_basis_credits_full_contract_once(client, env):
    h = env["h"]
    client.put(
        f"{API}/settings/finance",
        headers=h,
        json={"commission_basis": "assinatura", "default_commission_pct": "5", "reason": "teste"},
    )
    _, inst = env["contract"]()
    assert _balances(client, h, "comissao")["Corretora Quatro"]["pending_total"] == "4000.00"  # 4% de 100.000
    _pay(client, h, inst[0]["id"], "10000.00")
    assert (
        _balances(client, h, "comissao")["Corretora Quatro"]["pending_total"] == "4000.00"
    )  # não credita de novo
    assert (
        _balances(client, h, "proprietario")["Dono da Gleba"]["pending_total"] == "1000.00"
    )  # repasse segue o recebido


def test_broker_without_own_pct_uses_default_and_rules_are_frozen_at_signing(client, env):
    h = env["h"]
    other = client.post(f"{API}/brokers", headers=h, json={"name": "Corretor Padrão"}).json()
    _, inst = env["contract"](1, broker_id=other["id"])
    # mudar o padrão e o % do proprietário depois da assinatura não afeta o contrato
    client.put(
        f"{API}/settings/finance",
        headers=h,
        json={"commission_basis": "recebimento", "default_commission_pct": "9", "reason": "teste"},
    )
    client.put(
        f"{API}/developments/{env['dev']['id']}/owner-share",
        headers=h,
        json={"owner_share_pct": "50", "reason": "x y"},
    )
    _pay(client, h, inst[0]["id"], "10000.00")
    assert (
        _balances(client, h, "comissao")["Corretor Padrão"]["pending_total"] == "500.00"
    )  # 5% (padrão na assinatura)
    assert _balances(client, h, "proprietario")["Dono da Gleba"]["pending_total"] == "1000.00"  # 10%


def test_contract_without_broker_has_no_commission(client, env):
    h = env["h"]
    _, inst = env["contract"](broker_id=None)
    _pay(client, h, inst[0]["id"], "10000.00")
    assert _balances(client, h, "comissao") == {}
    assert _balances(client, h, "proprietario")["Dono da Gleba"]["pending_total"] == "1000.00"


def test_payment_reversal_reverses_credits(client, env):
    h = env["h"]
    _, inst = env["contract"]()
    pay_id = _pay(client, h, inst[0]["id"], "10000.00")["payments"][0]["id"]
    client.post(f"{API}/payments/{pay_id}/reverse", headers=h, json={"reason": "devolvido"})
    com = _balances(client, h, "comissao")["Corretora Quatro"]
    assert com["pending_total"] == "0.00" and com["pending_count"] == 2
    entries = client.get(f"{API}/participations", headers=h, params={"kind": "comissao"}).json()
    assert sorted(e["amount"] for e in entries) == ["-400.00", "400.00"]
    assert [e["is_reversal"] for e in entries if e["amount"] == "-400.00"] == [True]


# ─────────────────────────── acerto e contas a pagar ───────────────────────────


def test_settle_creates_payable_and_cancel_releases_credits(client, env):
    h = env["h"]
    _, inst = env["contract"]()
    _pay(client, h, inst[0]["id"], "10000.00")
    body = {"kind": "comissao", "beneficiary_id": env["broker"]["id"]}
    r = client.post(f"{API}/participations/settle", headers=h, json=body)
    assert r.status_code == 201, r.text
    pay = r.json()
    assert (
        pay["amount"] == "400.00"
        and pay["category"] == "comissao"
        and pay["payee_name"] == "Corretora Quatro"
    )
    assert pay["participations_count"] == 1 and pay["events"][0]["kind"] == "criada"
    assert (
        client.post(f"{API}/participations/settle", headers=h, json=body).status_code == 409
    )  # nada pendente
    assert _balances(client, h, "comissao")["Corretora Quatro"]["pending_total"] == "0.00"
    r = client.post(f"{API}/payables/{pay['id']}/cancel", headers=h, json={"reason": "gerado errado"})
    assert r.json()["status"] == "cancelada"
    assert _balances(client, h, "comissao")["Corretora Quatro"]["pending_total"] == "400.00"


def test_reversal_after_commission_paid_nets_next_settlement(client, env):
    h = env["h"]
    _, inst = env["contract"]()
    pay_entrada = _pay(client, h, inst[0]["id"], "10000.00")["payments"][0]["id"]
    payable = client.post(
        f"{API}/participations/settle",
        headers=h,
        json={"kind": "comissao", "beneficiary_id": env["broker"]["id"]},
    ).json()
    client.post(f"{API}/payables/{payable['id']}/pay", headers=h, json={"method": "pix"})
    client.post(f"{API}/payments/{pay_entrada}/reverse", headers=h, json={"reason": "devolvido"})
    _pay(client, h, inst[1]["id"], "9000.00")
    bal = _balances(client, h, "comissao")["Corretora Quatro"]
    assert bal["pending_total"] == "-40.00"  # -400 do estorno + 360 da nova parcela
    assert bal["paid_total"] == "400.00"
    assert (
        client.post(
            f"{API}/participations/settle",
            headers=h,
            json={"kind": "comissao", "beneficiary_id": env["broker"]["id"]},
        ).status_code
        == 409
    )  # saldo negativo: não gera conta


def test_manual_payable_lifecycle(client, env):
    h = env["h"]
    due = (_today() + timedelta(days=5)).isoformat()
    r = client.post(
        f"{API}/payables",
        headers=h,
        json={
            "description": "Topografia quadra D",
            "category": "obra",
            "payee_name": "Topógrafo Fictício",
            "development_id": env["dev"]["id"],
            "due_date": due,
            "amount": "3500.00",
        },
    )
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["status"] == "aberta" and p["development_name"] == "Loteamento Repasse"
    future = (_today() + timedelta(days=1)).isoformat()
    assert (
        client.post(f"{API}/payables/{p['id']}/pay", headers=h, json={"paid_on": future}).status_code == 409
    )
    paid = client.post(f"{API}/payables/{p['id']}/pay", headers=h, json={"method": "boleto"}).json()
    assert paid["status"] == "paga" and paid["paid_on"] == _today().isoformat() and paid["method"] == "boleto"
    assert (
        client.post(f"{API}/payables/{p['id']}/cancel", headers=h, json={"reason": "abc"}).status_code == 409
    )
    rev = client.post(
        f"{API}/payables/{p['id']}/reverse", headers=h, json={"reason": "pago em duplicidade"}
    ).json()
    assert rev["status"] == "aberta" and rev["paid_on"] is None
    assert [e["kind"] for e in rev["events"]] == ["estorno", "pagamento", "criada"]
    # comissão/repasse só nascem do acerto
    bad = {
        "description": "x y z",
        "category": "comissao",
        "payee_name": "Fulano",
        "due_date": due,
        "amount": "1",
    }
    assert client.post(f"{API}/payables", headers=h, json=bad).status_code == 422


def test_payables_summary_and_filters(client, env, owner_engine):
    h = env["h"]
    base = {"category": "fornecedor", "payee_name": "Fornecedor Fictício", "amount": "100.00"}
    a = client.post(
        f"{API}/payables",
        headers=h,
        json={**base, "description": "Vencida", "due_date": _today().isoformat()},
    ).json()
    b = client.post(
        f"{API}/payables",
        headers=h,
        json={**base, "description": "Futura", "due_date": (_today() + timedelta(days=10)).isoformat()},
    ).json()
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE payables SET due_date = :d WHERE id = :i"),
            {"d": _today() - timedelta(days=3), "i": a["id"]},
        )
    client.post(f"{API}/payables/{b['id']}/pay", headers=h, json={})
    s = client.get(f"{API}/payables/summary", headers=h).json()
    assert (s["open_count"], s["overdue_count"], s["overdue_total"], s["paid_month"]) == (
        1,
        1,
        "100.00",
        "100.00",
    )
    ids = lambda **p: [x["id"] for x in client.get(f"{API}/payables", headers=h, params=p).json()]  # noqa: E731
    assert ids(status="vencida") == [a["id"]] and ids(status="paga") == [b["id"]]
    assert ids(q="futura") == [b["id"]]


# ─────────────────────────── integridade, isolamento e RBAC ───────────────────────────


def test_credits_and_events_are_protected(app_engine, client, env, world):
    h = env["h"]
    _, inst = env["contract"]()
    _pay(client, h, inst[0]["id"], "10000.00")
    for sql in (
        "UPDATE participations SET amount = 1",
        "DELETE FROM participations",
        "UPDATE payable_events SET reason = 'x'",
        "DELETE FROM payables",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
            c.execute(text(sql))


def test_tenant_b_cannot_see_or_act(client, env):
    h = env["h"]
    _, inst = env["contract"]()
    _pay(client, h, inst[0]["id"], "10000.00")
    pay = client.post(
        f"{API}/participations/settle",
        headers=h,
        json={"kind": "comissao", "beneficiary_id": env["broker"]["id"]},
    ).json()
    bob = login(client, "bob", "empresa-b")
    assert client.get(f"{API}/participations/balances", headers=bob, params={"kind": "comissao"}).json() == []
    assert client.get(f"{API}/payables", headers=bob).json() == []
    assert client.get(f"{API}/payables/{pay['id']}", headers=bob).status_code == 404
    assert client.post(f"{API}/payables/{pay['id']}/pay", headers=bob, json={}).status_code == 404
    r = client.post(
        f"{API}/participations/settle",
        headers=bob,
        json={"kind": "comissao", "beneficiary_id": env["broker"]["id"]},
    )
    assert r.status_code == 404
    assert (
        client.put(
            f"{API}/developments/{env['dev']['id']}/owner-share",
            headers=bob,
            json={"owner_share_pct": "1", "reason": "abc"},
        ).status_code
        == 404
    )


def test_rls_hides_3b_tables_without_context(app_engine, client, env):
    _, inst = env["contract"]()
    _pay(client, env["h"], inst[0]["id"], "10000.00")
    with app_engine.begin() as c:
        for table in ("participations", "payables", "payable_events", "finance_settings"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608


def test_rbac_for_payables(client, env):
    for user in ("carol", "dave", "gil"):
        hh = login(client, user)
        assert client.get(f"{API}/payables", headers=hh).status_code == 403
        assert (
            client.get(f"{API}/participations/balances", headers=hh, params={"kind": "comissao"}).status_code
            == 403
        )
    fabi = login(client, "fabi")
    body = {
        "description": "Conta de luz",
        "category": "outro",
        "payee_name": "Distribuidora",
        "due_date": _today().isoformat(),
        "amount": "50.00",
    }
    p = client.post(f"{API}/payables", headers=fabi, json=body).json()
    assert client.post(f"{API}/payables/{p['id']}/pay", headers=fabi, json={}).status_code == 200
    assert (
        client.post(f"{API}/payables/{p['id']}/reverse", headers=fabi, json={"reason": "abc"}).status_code
        == 200
    )
    # % do proprietário: só owner/admin (owners:manage)
    r = client.put(
        f"{API}/developments/{env['dev']['id']}/owner-share",
        headers=fabi,
        json={"owner_share_pct": "1", "reason": "abc"},
    )
    assert r.status_code == 403
