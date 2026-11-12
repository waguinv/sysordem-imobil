"""Fase 3c: renegociação, distrato e relatórios — regras, travas, isolamento e RBAC."""

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
def env(client, owner_engine):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Loteamento Distrato"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "E"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 3, "area_m2": "300", "price": "100000.00"},
    ).json()
    customer = client.post(
        f"{API}/customers", headers=h, json={"name": "Comprador Distrato", "phone": "11 9000"}
    ).json()

    def contract(lot_idx: int = 0, sign: bool = True, **terms):
        res = client.post(
            f"{API}/reservations",
            headers=h,
            json={"lot_id": lots[lot_idx]["id"], "customer_id": customer["id"]},
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
        if sign:
            assert (
                client.post(
                    f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1}
                ).status_code
                == 200
            )
        inst = client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json() if sign else []
        return c, inst

    def make_late(inst_id: str, days: int) -> None:
        with owner_engine.begin() as conn:
            conn.execute(
                text("UPDATE installments SET due_date = :d WHERE id = :i"),
                {"d": _today() - timedelta(days=days), "i": inst_id},
            )

    return {"h": h, "contract": contract, "late": make_late, "lots": lots, "customer": customer, "dev": dev}


def _pay(client, h, inst_id, amount):
    r = client.post(f"{API}/installments/{inst_id}/payments", headers=h, json={"amount": amount})
    assert r.status_code == 201, r.text
    return r.json()


def _reneg(client, h, cid, **kw):
    body = {
        "installments_count": 20,
        "first_due_date": (_today() + timedelta(days=15)).isoformat(),
        "reason": "cliente pediu prazo",
        **kw,
    }
    return client.post(f"{API}/contracts/{cid}/renegotiations", headers=h, json=body)


# ─────────────────────────── renegociação ───────────────────────────


def test_renegotiation_replaces_open_balance_with_charges_and_discount(client, env):
    h = env["h"]
    c, inst = env["contract"]()
    _pay(client, h, inst[0]["id"], "10000.00")
    _pay(client, h, inst[2]["id"], "1000.00")  # parcial: só o restante entra
    env["late"](inst[1]["id"], 30)
    ob = client.get(f"{API}/contracts/{c['id']}/open-balance", headers=h).json()
    assert ob["count"] == 10 and ob["overdue_count"] == 1 and ob["balance"] == "89000.00"
    assert ob["late_charges"] == "270.00"  # 9.000 × (2% + 1%·30/30)
    r = _reneg(client, h, c["id"], discount="270.00")
    assert r.status_code == 201, r.text
    rn = r.json()
    assert (rn["old_balance"], rn["late_charges"], rn["discount"], rn["new_balance"]) == (
        "89000.00",
        "270.00",
        "270.00",
        "89000.00",
    )
    assert rn["replaced_count"] == 10 and rn["author_name"] == "Alice"
    rows = client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json()
    old = [x for x in rows if 1 <= x["number"] <= 10]
    new = [x for x in rows if x["number"] > 10]
    assert {x["status"] for x in old} == {"renegociada"}
    assert old[1]["paid_principal"] == "1000.00"  # o que já foi pago continua registrado
    assert [x["number"] for x in new] == list(range(11, 31))
    assert sum(D(x["principal"]) for x in new) == D("89000.00")
    assert client.get(f"{API}/receivables/summary", headers=h).json()["open_count"] == 20


def test_renegotiation_without_late_charges(client, env):
    h = env["h"]
    c, inst = env["contract"]()
    env["late"](inst[1]["id"], 10)
    rn = _reneg(client, h, c["id"], include_late_charges=False, discount="1000.00").json()
    assert rn["late_charges"] == "0.00" and rn["new_balance"] == "99000.00"


@pytest.mark.parametrize(
    ("over", "msg"),
    [
        ({"installments_count": 241}, "parcelas"),
        ({"interest_monthly_pct": "3"}, "Juros acima"),
        ({"discount": "100000.00"}, "Desconto"),
    ],
)
def test_renegotiation_limits(client, env, over, msg):
    c, _ = env["contract"]()
    r = _reneg(client, env["h"], c["id"], **over)
    assert r.status_code == 409 and msg in r.json()["detail"]


def test_renegotiation_requires_signed_contract_and_open_balance(client, env):
    h = env["h"]
    draft, _ = env["contract"](0, sign=False)
    assert _reneg(client, h, draft["id"]).status_code == 409
    cash, inst = env["contract"](1, down_payment="100000.00", installments_count=0, first_due_date=None)
    _pay(client, h, inst[0]["id"], "100000.00")
    r = _reneg(client, h, cash["id"])
    assert r.status_code == 409 and "Não há parcelas" in r.json()["detail"]
    past = (_today() - timedelta(days=1)).isoformat()
    c, _ = env["contract"](2)
    assert _reneg(client, h, c["id"], first_due_date=past).status_code == 409


def test_payment_on_renegotiated_installment_cannot_be_reversed(client, env):
    h = env["h"]
    c, inst = env["contract"]()
    pay_id = _pay(client, h, inst[1]["id"], "500.00")["payments"][0]["id"]
    _reneg(client, h, c["id"])
    r = client.post(f"{API}/payments/{pay_id}/reverse", headers=h, json={"reason": "teste"})
    assert r.status_code == 409 and "renegociada" in r.json()["detail"]


# ─────────────────────────── distrato ───────────────────────────


def test_rescission_retains_and_refunds(client, env):
    h = env["h"]
    c, inst = env["contract"]()
    _pay(client, h, inst[0]["id"], "10000.00")
    _pay(client, h, inst[1]["id"], "9000.00")
    q = client.get(f"{API}/contracts/{c['id']}/rescission-quote", headers=h).json()
    assert (q["paid_total"], q["retention_amount"], q["refund_amount"], q["open_count"]) == (
        "19000.00",
        "10000.00",
        "9000.00",
        9,
    )
    due = (_today() + timedelta(days=60)).isoformat()
    r = client.post(
        f"{API}/contracts/{c['id']}/rescind",
        headers=h,
        json={"reason": "desistência do comprador", "refund_due_date": due},
    )
    assert r.status_code == 200, r.text
    rs = r.json()
    assert rs["refund_amount"] == "9000.00" and rs["payable_id"] and rs["author_name"] == "Alice"
    detail = client.get(f"{API}/contracts/{c['id']}", headers=h).json()
    assert detail["status"] == "distratado"
    rows = client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json()
    assert {x["status"] for x in rows if x["number"] >= 2} == {"cancelada"}
    lot = client.get(f"{API}/lots/{c['lot_id']}", headers=h).json()
    assert lot["status"] == "disponivel"
    pay = client.get(f"{API}/payables/{rs['payable_id']}", headers=h).json()
    assert (pay["category"], pay["amount"], pay["due_date"], pay["payee_name"]) == (
        "devolucao",
        "9000.00",
        due,
        "Comprador Distrato",
    )
    assert client.get(f"{API}/contracts/{c['id']}/rescission", headers=h).json()["retention_pct"] == "10.00"
    # distrato é final
    assert (
        client.post(f"{API}/contracts/{c['id']}/rescind", headers=h, json={"reason": "de novo"}).status_code
        == 409
    )
    assert _reneg(client, h, c["id"]).status_code == 409


def test_rescission_retention_capped_at_paid_and_lot_resold(client, env):
    h = env["h"]
    c, inst = env["contract"]()
    _pay(client, h, inst[0]["id"], "5000.00")
    rs = client.post(f"{API}/contracts/{c['id']}/rescind", headers=h, json={"reason": "inadimplência"}).json()
    assert (rs["retention_amount"], rs["refund_amount"], rs["payable_id"]) == ("5000.00", "0.00", None)
    # o lote pode ser vendido de novo
    again, _ = env["contract"](0)
    assert client.get(f"{API}/contracts/{again['id']}", headers=h).json()["status"] == "assinado"


def test_custom_retention_and_sold_lot_has_no_manual_exit(client, env):
    h = env["h"]
    c, inst = env["contract"]()
    lot = client.get(f"{API}/lots/{c['lot_id']}", headers=h).json()
    assert lot["status"] == "vendido" and lot["allowed_transitions"] == []
    r = client.post(
        f"{API}/lots/{c['lot_id']}/status",
        headers=h,
        json={"to_status": "disponivel", "expected_version": lot["version"], "reason": "manual"},
    )
    assert r.status_code == 422
    _pay(client, h, inst[0]["id"], "10000.00")
    q = client.get(
        f"{API}/contracts/{c['id']}/rescission-quote", headers=h, params={"retention_pct": "5"}
    ).json()
    assert (q["retention_amount"], q["refund_amount"]) == ("5000.00", "5000.00")


# ─────────────────────────── relatórios ───────────────────────────


def test_cash_flow_report(client, env):
    h = env["h"]
    c, inst = env["contract"]()
    _pay(client, h, inst[0]["id"], "10000.00")
    client.post(
        f"{API}/payables",
        headers=h,
        json={
            "description": "Topografia",
            "category": "obra",
            "payee_name": "Topógrafo",
            "due_date": _today().isoformat(),
            "amount": "2500.00",
        },
    )
    pay = client.get(f"{API}/payables", headers=h).json()[0]
    client.post(f"{API}/payables/{pay['id']}/pay", headers=h, json={})
    r = client.get(
        f"{API}/reports/cash-flow", headers=h, params={"months_back": 1, "months_ahead": 12}
    ).json()
    months = {m["month"]: m for m in r["months"]}
    cur = months[_today().strftime("%Y-%m")]
    assert (cur["received"], cur["paid"], cur["net"]) == ("10000.00", "2500.00", "7500.00")
    assert sum(D(m["expected_in"]) for m in r["months"]) == D("90000.00")
    assert len(r["months"]) == 14


def test_delinquency_report(client, env):
    h = env["h"]
    c, inst = env["contract"]()
    env["late"](inst[0]["id"], 45)  # entrada vencida há 45 dias
    env["late"](inst[1]["id"], 10)
    r = client.get(f"{API}/reports/delinquency", headers=h).json()
    assert r["contracts"] == 1 and r["total"] == "19000.00"
    item = r["items"][0]
    assert (item["overdue_count"], item["days_late"], item["bucket"], item["customer_phone"]) == (
        2,
        45,
        "31-60",
        "11 9000",
    )
    assert r["buckets"]["31-60"] == "19000.00" and r["buckets"]["1-30"] == "0.00"


def test_sales_report(client, env):
    h = env["h"]
    env["contract"](0)
    c2, _ = env["contract"](1, total_price="90000.00")
    client.post(f"{API}/contracts/{c2['id']}/rescind", headers=h, json={"reason": "teste"})
    env["contract"](2, sign=False)  # rascunho não conta
    r = client.get(f"{API}/reports/sales", headers=h).json()
    row = r["rows"][0]
    assert (row["contracts"], row["total_value"], row["down_payments"], row["rescissions"]) == (
        2,
        "190000.00",
        "20000.00",
        1,
    )
    assert r["contracts"] == 2


# ─────────────────────────── integridade, isolamento e RBAC ───────────────────────────


def test_changes_are_append_only(app_engine, client, env, world):
    h = env["h"]
    c, _ = env["contract"]()
    _reneg(client, h, c["id"])
    client.post(f"{API}/contracts/{c['id']}/rescind", headers=h, json={"reason": "teste"})
    for sql in ("UPDATE renegotiations SET discount = 0", "DELETE FROM rescissions"):
        with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
            conn.execute(text(sql))
    with app_engine.begin() as conn:
        for table in ("renegotiations", "rescissions"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608 (sem contexto: RLS)


def test_tenant_b_cannot_see_or_change(client, env):
    c, inst = env["contract"]()
    env["late"](inst[0]["id"], 5)
    bob = login(client, "bob", "empresa-b")
    assert _reneg(client, bob, c["id"]).status_code == 404
    assert (
        client.post(f"{API}/contracts/{c['id']}/rescind", headers=bob, json={"reason": "abc"}).status_code
        == 404
    )
    assert client.get(f"{API}/contracts/{c['id']}/rescission-quote", headers=bob).status_code == 404
    assert client.get(f"{API}/reports/delinquency", headers=bob).json()["contracts"] == 0
    assert client.get(f"{API}/reports/sales", headers=bob).json()["contracts"] == 0
    flow = client.get(f"{API}/reports/cash-flow", headers=bob).json()
    assert all(m["expected_in"] == "0.00" for m in flow["months"])


def test_rbac_for_changes_and_reports(client, env):
    c, _ = env["contract"]()
    fabi, dave, carol = login(client, "fabi"), login(client, "dave"), login(client, "carol")
    assert (
        client.post(f"{API}/contracts/{c['id']}/rescind", headers=fabi, json={"reason": "abc"}).status_code
        == 403
    )
    assert (
        client.post(f"{API}/contracts/{c['id']}/rescind", headers=dave, json={"reason": "abc"}).status_code
        == 403
    )
    assert _reneg(client, dave, c["id"]).status_code == 403
    assert client.get(f"{API}/reports/cash-flow", headers=dave).status_code == 403
    assert client.get(f"{API}/reports/sales", headers=dave).status_code == 200  # comercial vê vendas
    assert client.get(f"{API}/reports/sales", headers=carol).status_code == 403
    assert _reneg(client, fabi, c["id"]).status_code == 201  # financeiro renegocia
    erin = login(client, "erin")
    assert (
        client.post(f"{API}/contracts/{c['id']}/rescind", headers=erin, json={"reason": "abc"}).status_code
        == 200
    )
