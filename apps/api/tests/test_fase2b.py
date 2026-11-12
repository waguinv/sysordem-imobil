"""Fase 2b: contratos — fluxo, versões imutáveis, numeração, concorrência, isolamento e RBAC."""

import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"
DUE = (datetime.now(UTC).date() + timedelta(days=30)).isoformat()


@pytest.fixture()
def env(client):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial Contrato"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "C"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 3, "area_m2": "300", "price": "120000.00"},
    ).json()
    customer = client.post(f"{API}/customers", headers=h, json={"name": "Comprador Contrato"}).json()
    res = [
        client.post(
            f"{API}/reservations", headers=h, json={"lot_id": lot["id"], "customer_id": customer["id"]}
        ).json()
        for lot in lots[:2]
    ]
    return {"h": h, "lots": lots, "customer": customer, "res": res}


def _terms(**over):
    t = {"down_payment": "20000.00", "installments_count": 100, "first_due_date": DUE}
    t.update(over)
    return t


def _create(client, h, reservation_id, **over):
    return client.post(
        f"{API}/contracts", headers=h, json={"reservation_id": reservation_id, **_terms(**over)}
    )


def _lot(client, h, lot_id):
    return client.get(f"{API}/lots/{lot_id}", headers=h).json()


# ─────────────────────────── fluxo ───────────────────────────


def test_create_contract_from_reservation(client, env):
    h = env["h"]
    r = _create(client, h, env["res"][0]["id"])
    assert r.status_code == 201, r.text
    c = r.json()
    year = datetime.now(UTC).year
    assert c["number"] == f"{year}-0001" and c["status"] == "rascunho" and c["current_version"] == 1
    t = c["terms"]
    assert t["total_price"] == "120000.00"  # preço congelado na reserva
    assert t["installment_amount"] == "1000.00"  # (120000 − 20000) / 100, sem juros
    assert t["late_fee_pct"] == "2.00" and t["adjustment_index"] == "nenhum"

    res = client.get(f"{API}/reservations/{env['res'][0]['id']}", headers=h).json()
    assert res["status"] == "convertida" and res["close_reason"] == f"contrato {c['number']}"
    lot = _lot(client, h, env["lots"][0]["id"])
    assert lot["status"] == "reservado"  # segue preso ao contrato até assinar
    assert lot["contract_id"] == c["id"] and lot["active_reservation_id"] is None
    assert lot["allowed_transitions"] == []


def test_numbers_are_sequential_per_tenant(client, env):
    h = env["h"]
    a = _create(client, h, env["res"][0]["id"]).json()
    b = _create(client, h, env["res"][1]["id"]).json()
    assert int(b["number"].split("-")[1]) == int(a["number"].split("-")[1]) + 1
    # empresa B começa sua própria sequência
    bob = login(client, "bob", "empresa-b")
    dev = client.post(f"{API}/developments", headers=bob, json={"name": "Emp B"}).json()
    blk = client.post(f"{API}/developments/{dev['id']}/blocks", headers=bob, json={"code": "B"}).json()
    lot = client.post(
        f"{API}/developments/{dev['id']}/lots",
        headers=bob,
        json={"block_id": blk["id"], "number": "1", "area_m2": "100", "price": "50000"},
    ).json()
    cust = client.post(f"{API}/customers", headers=bob, json={"name": "Cliente B"}).json()
    rb = client.post(
        f"{API}/reservations", headers=bob, json={"lot_id": lot["id"], "customer_id": cust["id"]}
    ).json()
    cb = _create(
        client, bob, rb["id"], down_payment="50000.00", installments_count=0, first_due_date=None
    ).json()
    assert cb["number"].endswith("-0001")


def test_price_table_with_interest(client, env):
    c = _create(
        client,
        env["h"],
        env["res"][0]["id"],
        down_payment="20000.00",
        installments_count=12,
        interest_monthly_pct="1",
    ).json()
    assert c["terms"]["installment_amount"] == "8884.88"


def test_cash_sale(client, env):
    r = _create(
        client,
        env["h"],
        env["res"][0]["id"],
        down_payment="120000.00",
        installments_count=0,
        first_due_date=None,
    )
    assert r.status_code == 201, r.text
    assert r.json()["terms"]["installment_amount"] == "0.00"


@pytest.mark.parametrize(
    ("over", "msg"),
    [
        ({"down_payment": "130000.00"}, "Entrada não pode ser maior"),
        ({"installments_count": 0}, "venda à vista"),
        ({"down_payment": "120000.00"}, "não há parcelas"),
        ({"first_due_date": None}, "vencimento"),
        ({"first_due_date": (date.today() - timedelta(days=2)).isoformat()}, "passado"),
    ],
)
def test_invalid_terms_rejected_without_side_effects(client, env, over, msg):
    h = env["h"]
    r = _create(client, h, env["res"][0]["id"], **over)
    assert r.status_code == 409 and msg in r.json()["detail"], r.text
    # nada mudou: reserva segue ativa e nenhum número foi consumido
    assert client.get(f"{API}/reservations/{env['res'][0]['id']}", headers=h).json()["status"] == "ativa"
    ok = _create(client, h, env["res"][0]["id"]).json()
    assert ok["number"].endswith("-0001")


def test_new_version_keeps_history_and_requires_current_version(client, env):
    h = env["h"]
    c = _create(client, h, env["res"][0]["id"]).json()
    url = f"{API}/contracts/{c['id']}/versions"
    r = client.post(
        url,
        headers=h,
        json={
            **_terms(down_payment="30000.00", installments_count=90),
            "expected_version": 1,
            "reason": "cliente aumentou a entrada",
        },
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["current_version"] == 2 and d["terms"]["installment_amount"] == "1000.00"
    assert [v["version"] for v in d["versions"]] == [2, 1]
    assert d["versions"][1]["down_payment"] == "20000.00"  # versão 1 preservada
    stale = client.post(url, headers=h, json={**_terms(), "expected_version": 1, "reason": "atrasado"})
    assert stale.status_code == 409


def test_sign_sells_lot_and_freezes_contract(client, env):
    h = env["h"]
    c = _create(client, h, env["res"][0]["id"]).json()
    # assinatura com versão desatualizada é recusada
    assert (
        client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 9}).status_code
        == 409
    )
    r = client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1})
    assert r.status_code == 200 and r.json()["status"] == "assinado" and r.json()["signed_at"]
    assert _lot(client, h, env["lots"][0]["id"])["status"] == "vendido"
    v = client.post(
        f"{API}/contracts/{c['id']}/versions",
        headers=h,
        json={**_terms(), "expected_version": 1, "reason": "mudar depois"},
    )
    assert v.status_code == 409
    cancel = client.post(f"{API}/contracts/{c['id']}/cancel", headers=h, json={"reason": "arrependimento"})
    assert cancel.status_code == 409 and "distrato" in cancel.json()["detail"]
    hist = client.get(f"{API}/lots/{env['lots'][0]['id']}/history", headers=h).json()
    assert hist[0]["reason"] == f"contrato {c['number']} assinado"


def test_cancel_draft_frees_lot(client, env):
    h = env["h"]
    c = _create(client, h, env["res"][0]["id"]).json()
    r = client.post(f"{API}/contracts/{c['id']}/cancel", headers=h, json={"reason": "desistência"})
    assert r.status_code == 200 and r.json()["status"] == "cancelado"
    lot = _lot(client, h, env["lots"][0]["id"])
    assert lot["status"] == "disponivel" and lot["contract_id"] is None
    # o lote pode ser reservado e contratado de novo
    res = client.post(
        f"{API}/reservations",
        headers=h,
        json={"lot_id": env["lots"][0]["id"], "customer_id": env["customer"]["id"]},
    ).json()
    assert _create(client, h, res["id"]).status_code == 201


def test_reservation_cannot_become_two_contracts(client, env):
    h = env["h"]
    assert _create(client, h, env["res"][0]["id"]).status_code == 201
    r = _create(client, h, env["res"][0]["id"])
    assert r.status_code == 409 and "não está ativa" in r.json()["detail"]


def test_expired_reservation_cannot_become_contract(client, env, owner_engine):
    h = env["h"]
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE reservations SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": env["res"][0]["id"]},
        )
    r = _create(client, h, env["res"][0]["id"])
    assert r.status_code == 409
    assert _lot(client, h, env["lots"][0]["id"])["status"] == "disponivel"


def test_contract_versions_are_append_only(app_engine, client, env, world):
    _create(client, env["h"], env["res"][0]["id"])
    for stmt in ("UPDATE contract_versions SET down_payment = 0", "DELETE FROM contract_versions"):
        with pytest.raises(Exception, match="permission denied"), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
            c.execute(text(stmt))


def test_concurrent_contracts_from_same_reservation_only_one_wins(env, world):
    from app.core.db import get_sessionmaker, set_tenant
    from app.services import contracts as svc
    from app.services.contracts import Terms

    res_id = uuid.UUID(env["res"][1]["id"])
    terms = Terms(
        total_price=None,
        down_payment=Decimal("20000"),
        installments_count=10,
        interest_monthly_pct=Decimal("0"),
        first_due_date=datetime.now(UTC).date() + timedelta(days=30),
        adjustment_index="nenhum",
        late_fee_pct=Decimal("2"),
        late_interest_monthly_pct=Decimal("1"),
        notes=None,
    )
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def run(actor: str) -> None:
        db = get_sessionmaker()()
        try:
            set_tenant(db, world.tenant_a)
            barrier.wait()
            svc.create_from_reservation(
                db, tenant_id=world.tenant_a, reservation_id=res_id, terms=terms, actor=world.users[actor]
            )
            time.sleep(0.3)
            db.commit()
            outcome = "ok"
        except svc.ContractError:
            db.rollback()
            outcome = "conflict"
        finally:
            db.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=run, args=(u,)) for u in ("alice", "dave")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert sorted(results) == ["conflict", "ok"]


# ─────────────────────────── isolamento e RBAC ───────────────────────────


def test_tenant_b_cannot_see_or_act_on_contracts(client, env):
    c = _create(client, env["h"], env["res"][0]["id"]).json()
    bob = login(client, "bob", "empresa-b")
    assert client.get(f"{API}/contracts", headers=bob).json() == []
    assert client.get(f"{API}/contracts/{c['id']}", headers=bob).status_code == 404
    for path, body in (("sign", {"expected_version": 1}), ("cancel", {"reason": "invasão"})):
        assert client.post(f"{API}/contracts/{c['id']}/{path}", headers=bob, json=body).status_code == 404
    assert _create(client, bob, env["res"][1]["id"]).status_code == 404


def test_rls_hides_contract_tables_without_context(app_engine, client, env):
    _create(client, env["h"], env["res"][0]["id"])
    with app_engine.begin() as c:
        for table in ("sales_contracts", "contract_versions", "tenant_counters"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608


def test_rbac_for_contracts(client, env):
    c = _create(client, login(client, "dave"), env["res"][0]["id"])  # comercial gera contrato
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    dave = login(client, "dave")
    assert (
        client.post(f"{API}/contracts/{cid}/sign", headers=dave, json={"expected_version": 1}).status_code
        == 403
    )
    fabi = login(client, "fabi")  # financeiro só consulta
    assert client.get(f"{API}/contracts/{cid}", headers=fabi).status_code == 200
    assert (
        client.post(f"{API}/contracts/{cid}/cancel", headers=fabi, json={"reason": "xyz"}).status_code == 403
    )
    for user in ("carol", "gil"):
        assert client.get(f"{API}/contracts", headers=login(client, user)).status_code == 403
    erin = login(client, "erin")  # admin assina
    assert (
        client.post(f"{API}/contracts/{cid}/sign", headers=erin, json={"expected_version": 1}).status_code
        == 200
    )


def test_list_filters_by_status(client, env):
    h = env["h"]
    a = _create(client, h, env["res"][0]["id"]).json()
    b = _create(client, h, env["res"][1]["id"]).json()
    client.post(f"{API}/contracts/{b['id']}/sign", headers=h, json={"expected_version": 1})
    drafts = client.get(f"{API}/contracts", headers=h, params={"status": "rascunho"}).json()
    assert [c["id"] for c in drafts] == [a["id"]]
    assert len(client.get(f"{API}/contracts", headers=h).json()) == 2
