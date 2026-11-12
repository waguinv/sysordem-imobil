"""Fase 3a: cronograma, recebimentos (parciais, em atraso), estornos, concorrência, isolamento e RBAC."""

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
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial Parcelas"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "C"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 3, "area_m2": "300", "price": "120000.00"},
    ).json()
    customer = client.post(f"{API}/customers", headers=h, json={"name": "Comprador Parcelado"}).json()

    def contract(lot_idx: int = 0, sign: bool = True, **terms):
        res = client.post(
            f"{API}/reservations",
            headers=h,
            json={"lot_id": lots[lot_idx]["id"], "customer_id": customer["id"]},
        ).json()
        body = {
            "reservation_id": res["id"],
            "down_payment": "20000.00",
            "installments_count": 12,
            "interest_monthly_pct": "1",
            "first_due_date": (_today() + timedelta(days=30)).isoformat(),
            **terms,
        }
        c = client.post(f"{API}/contracts", headers=h, json=body)
        assert c.status_code == 201, c.text
        c = c.json()
        if sign:
            r = client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1})
            assert r.status_code == 200, r.text
        return c

    return {"h": h, "contract": contract, "lots": lots}


def _installments(client, h, contract_id):
    r = client.get(f"{API}/contracts/{contract_id}/installments", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _pay(client, h, inst_id, amount, **extra):
    return client.post(f"{API}/installments/{inst_id}/payments", headers=h, json={"amount": amount, **extra})


def _make_late(owner_engine, inst_id, days=30):
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE installments SET due_date = :d WHERE id = :i"),
            {"d": _today() - timedelta(days=days), "i": inst_id},
        )


# ─────────────────────────── cronograma ───────────────────────────


def test_signing_generates_schedule(client, env):
    c = env["contract"]()
    rows = _installments(client, env["h"], c["id"])
    assert [r["number"] for r in rows] == list(range(0, 13))
    assert rows[0]["amount"] == "20000.00" and rows[0]["due_date"] == _today().isoformat()
    assert sum(D(r["principal"]) for r in rows) == D("120000.00")
    assert {r["amount"] for r in rows[1:-1]} == {"8884.88"}
    assert all(r["status"] == "aberta" and r["remaining"] == r["amount"] for r in rows)
    assert rows[1]["contract_number"] == c["number"] and rows[1]["customer_name"] == "Comprador Parcelado"


def test_draft_contract_has_no_schedule(client, env):
    c = env["contract"](sign=False)
    assert _installments(client, env["h"], c["id"]) == []


def test_sac_and_cash_sale_schedules(client, env):
    sac = env["contract"](0, amortization="sac")
    rows = _installments(client, env["h"], sac["id"])[1:]
    assert rows[0]["amount"] == "9333.33" and D(rows[-1]["amount"]) < D(rows[0]["amount"])
    cash = env["contract"](1, down_payment="120000.00", installments_count=0, first_due_date=None)
    assert [(r["number"], r["amount"]) for r in _installments(client, env["h"], cash["id"])] == [
        (0, "120000.00")
    ]


def test_legacy_signed_contract_gets_schedule_on_first_read(client, env, owner_engine):
    c = env["contract"]()
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM installments WHERE contract_id = :c"), {"c": c["id"]})
    assert len(_installments(client, env["h"], c["id"])) == 13
    assert len(_installments(client, env["h"], c["id"])) == 13  # idempotente


# ─────────────────────────── recebimentos ───────────────────────────


def test_full_payment_on_time_settles_installment(client, env):
    h = env["h"]
    entrada = _installments(client, h, env["contract"]()["id"])[0]
    r = _pay(client, h, entrada["id"], "20000.00", method="transferencia")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "paga" and d["remaining"] == "0.00" and d["paid_at"] == _today().isoformat()
    p = d["payments"][0]
    assert (p["principal"], p["late_fee"], p["late_interest"]) == ("20000.00", "0.00", "0.00")
    assert p["created_by_name"] == "Alice" and not p["reversed"]
    assert _pay(client, h, entrada["id"], "1.00").status_code == 409  # já paga


def test_partial_payments_and_limits(client, env):
    h = env["h"]
    inst = _installments(client, h, env["contract"]()["id"])[1]
    assert _pay(client, h, inst["id"], "8884.89").status_code == 409  # acima do devido
    future = (_today() + timedelta(days=1)).isoformat()
    assert _pay(client, h, inst["id"], "100.00", received_on=future).status_code == 409
    assert _pay(client, h, inst["id"], "0").status_code == 422
    d = _pay(client, h, inst["id"], "4000.00").json()
    assert d["status"] == "aberta" and d["remaining"] == "4884.88" and d["quote"]["total"] == "4884.88"
    d = _pay(client, h, inst["id"], "4884.88", method="boleto").json()
    assert d["status"] == "paga" and len(d["payments"]) == 2


def test_late_payment_charges_fee_and_pro_rata_interest(client, env, owner_engine):
    h = env["h"]
    inst = _installments(client, h, env["contract"]()["id"])[1]
    _make_late(owner_engine, inst["id"], 30)
    q = client.get(f"{API}/installments/{inst['id']}/quote", headers=h).json()
    # multa 2% e mora 1% a.m. (padrões da política) sobre 8.884,88, 30 dias
    assert (q["late_fee"], q["late_interest"], q["days_late"]) == ("177.70", "88.85", 30)
    assert q["total"] == "9151.43"
    listed = client.get(f"{API}/installments", headers=h, params={"status": "vencida"}).json()
    assert [x["id"] for x in listed] == [inst["id"]] and listed[0]["days_late"] == 30
    # pagamento parcial em atraso: encargos só sobre a parte quitada
    d = _pay(client, h, inst["id"], "1030.00").json()
    p = d["payments"][0]
    assert (p["principal"], p["late_fee"], p["late_interest"]) == ("1000.00", "20.00", "10.00")
    assert d["remaining"] == "7884.88"
    # quitar o restante na mesma data
    rest = client.get(f"{API}/installments/{inst['id']}/quote", headers=h).json()["total"]
    assert _pay(client, h, inst["id"], rest).json()["status"] == "paga"


def test_quote_for_a_past_date_before_due_has_no_charges(client, env):
    h = env["h"]
    inst = _installments(client, h, env["contract"]()["id"])[2]
    q = client.get(
        f"{API}/installments/{inst['id']}/quote", headers=h, params={"on": inst["due_date"]}
    ).json()
    assert q["total"] == inst["amount"] and q["days_late"] == 0


# ─────────────────────────── estornos ───────────────────────────


def test_reversal_restores_installment_once(client, env):
    h = env["h"]
    entrada = _installments(client, h, env["contract"]()["id"])[0]
    pay_id = _pay(client, h, entrada["id"], "20000.00").json()["payments"][0]["id"]
    assert client.post(f"{API}/payments/{pay_id}/reverse", headers=h, json={"reason": "x"}).status_code == 422
    r = client.post(f"{API}/payments/{pay_id}/reverse", headers=h, json={"reason": "cheque devolvido"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "aberta" and d["remaining"] == "20000.00" and d["paid_at"] is None
    assert d["payments"][0]["reversed"] and d["payments"][0]["reversal_reason"] == "cheque devolvido"
    again = client.post(f"{API}/payments/{pay_id}/reverse", headers=h, json={"reason": "de novo"})
    assert again.status_code == 409
    assert _pay(client, h, entrada["id"], "20000.00").json()["status"] == "paga"  # pode receber de novo


def test_summary_counts_open_overdue_and_net_received(client, env, owner_engine):
    h = env["h"]
    rows = _installments(client, h, env["contract"]()["id"])
    _make_late(owner_engine, rows[1]["id"], 10)
    _pay(client, h, rows[0]["id"], "20000.00")
    reversed_pay = _pay(client, h, rows[2]["id"], "1000.00").json()["payments"][0]["id"]
    client.post(f"{API}/payments/{reversed_pay}/reverse", headers=h, json={"reason": "lançado errado"})
    s = client.get(f"{API}/receivables/summary", headers=h).json()
    assert s["open_count"] == 12 and D(s["open_total"]) == sum(D(r["amount"]) for r in rows[1:])
    assert s["overdue_count"] == 1 and s["overdue_total"] == rows[1]["amount"]
    assert s["received_month"] == "20000.00"  # estornado não conta
    assert s["due_30_count"] == 0
    _make_late(owner_engine, rows[2]["id"], -10)  # passa a vencer daqui a 10 dias
    s = client.get(f"{API}/receivables/summary", headers=h).json()
    assert s["due_30_count"] == 1 and s["due_30_total"] == rows[2]["amount"]


def test_list_filters(client, env):
    h = env["h"]
    rows = _installments(client, h, env["contract"]()["id"])
    _pay(client, h, rows[0]["id"], "20000.00")
    ids = lambda **p: [x["id"] for x in client.get(f"{API}/installments", headers=h, params=p).json()]  # noqa: E731
    assert ids(status="paga") == [rows[0]["id"]]
    assert len(ids(status="a_vencer")) == 12
    assert len(ids(q="parcelado")) == 13
    assert ids(due_to=rows[1]["due_date"], status="aberta") == [rows[1]["id"]]


# ─────────────────────────── integridade ───────────────────────────


def test_payments_and_reversals_are_append_only(app_engine, client, env, world):
    h = env["h"]
    entrada = _installments(client, h, env["contract"]()["id"])[0]
    pay_id = _pay(client, h, entrada["id"], "20000.00").json()["payments"][0]["id"]
    client.post(f"{API}/payments/{pay_id}/reverse", headers=h, json={"reason": "teste"})
    for sql in (
        "UPDATE payments SET amount = 1",
        "DELETE FROM payments",
        "UPDATE payment_reversals SET reason = 'x'",
        "DELETE FROM payment_reversals",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
            c.execute(text(sql))


def test_concurrent_payments_cannot_exceed_installment(client, env, world):
    from app.core.db import get_sessionmaker, set_tenant
    from app.services import receivables as svc

    inst_id = uuid.UUID(_installments(client, env["h"], env["contract"]()["id"])[0]["id"])
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def pay() -> None:
        db = get_sessionmaker()()
        try:
            set_tenant(db, world.tenant_a)
            barrier.wait()
            svc.register_payment(
                db,
                tenant_id=world.tenant_a,
                installment_id=inst_id,
                amount=D("20000.00"),
                received_on=_today(),
                method="pix",
                notes=None,
                actor=world.users["fabi"],
            )
            time.sleep(0.3)
            db.commit()
            outcome = "ok"
        except svc.ReceivableError:
            db.rollback()
            outcome = "rejected"
        finally:
            db.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=pay) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(results) == ["ok", "rejected"]


def test_tenant_b_cannot_see_or_pay(client, env):
    h = env["h"]
    c = env["contract"]()
    inst = _installments(client, h, c["id"])[0]
    pay_id = _pay(client, h, inst["id"], "100.00").json()["payments"][0]["id"]
    bob = login(client, "bob", "empresa-b")
    assert client.get(f"{API}/installments", headers=bob).json() == []
    assert client.get(f"{API}/installments/{inst['id']}", headers=bob).status_code == 404
    assert client.get(f"{API}/contracts/{c['id']}/installments", headers=bob).status_code == 404
    assert _pay(client, bob, inst["id"], "1.00").status_code == 404
    assert (
        client.post(f"{API}/payments/{pay_id}/reverse", headers=bob, json={"reason": "abc"}).status_code
        == 404
    )
    assert client.get(f"{API}/receivables/summary", headers=bob).json()["open_count"] == 0


def test_rls_hides_finance_tables_without_context(app_engine, client, env):
    inst = _installments(client, env["h"], env["contract"]()["id"])[0]
    _pay(client, env["h"], inst["id"], "100.00")
    with app_engine.begin() as c:
        for table in ("installments", "payments", "payment_reversals"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608


def test_rbac_for_finance(client, env):
    c = env["contract"]()
    inst = _installments(client, env["h"], c["id"])[0]
    for user in ("carol", "dave", "gil"):  # leitura, comercial e corretor: sem financeiro
        hh = login(client, user)
        assert client.get(f"{API}/installments", headers=hh).status_code == 403
        assert _pay(client, hh, inst["id"], "1.00").status_code == 403
    fabi = login(client, "fabi")  # financeiro: recebe e estorna
    pay_id = _pay(client, fabi, inst["id"], "500.00").json()["payments"][0]["id"]
    r = client.post(f"{API}/payments/{pay_id}/reverse", headers=fabi, json={"reason": "duplicado"})
    assert r.status_code == 200
