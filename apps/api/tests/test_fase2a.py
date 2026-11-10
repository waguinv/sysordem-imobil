"""Fase 2a: compradores, corretores e reservas — regras, expiração, concorrência, isolamento e RBAC."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"


@pytest.fixture()
def env(client):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial Reserva"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "A"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 4, "area_m2": "250", "price": "99000.90"},
    ).json()
    customer = client.post(
        f"{API}/customers", headers=h, json={"name": "Comprador Fictício", "document": "529.982.247-25"}
    ).json()
    broker = client.post(
        f"{API}/brokers", headers=h, json={"name": "Corretor Fictício", "creci": "12345-F"}
    ).json()
    return {"h": h, "dev": dev, "lots": lots, "customer": customer, "broker": broker}


def _reserve(client, h, lot_id, customer_id, **extra):
    return client.post(
        f"{API}/reservations", headers=h, json={"lot_id": lot_id, "customer_id": customer_id, **extra}
    )


def _expire_in_db(owner_engine, res_id):
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE reservations SET expires_at = now() - interval '1 minute' WHERE id = :i"),
            {"i": res_id},
        )


# ─────────────────────────── reserva: fluxo básico ───────────────────────────


def test_create_reservation_reserves_lot_with_price_snapshot(client, env):
    lot = env["lots"][0]
    r = _reserve(client, env["h"], lot["id"], env["customer"]["id"], broker_id=env["broker"]["id"])
    assert r.status_code == 201, r.text
    res = r.json()
    assert res["status"] == "ativa"
    assert res["price"] == "99000.90"
    assert res["customer_name"] == "Comprador Fictício" and res["broker_name"] == "Corretor Fictício"
    assert res["block_code"] == "A" and res["lot_number"] == "01"
    expires = datetime.fromisoformat(res["expires_at"])
    assert timedelta(hours=71) < expires - datetime.now(UTC) <= timedelta(hours=72)

    detail = client.get(f"{API}/lots/{lot['id']}", headers=env["h"]).json()
    assert detail["status"] == "reservado"
    assert detail["active_reservation_id"] == res["id"]
    assert detail["allowed_transitions"] == []  # só sai de "reservado" pela reserva
    r = client.post(
        f"{API}/lots/{lot['id']}/status",
        headers=env["h"],
        json={"to_status": "disponivel", "expected_version": detail["version"], "reason": "manual"},
    )
    assert r.status_code == 422

    hist = client.get(f"{API}/lots/{lot['id']}/history", headers=env["h"]).json()
    assert hist[0]["to_status"] == "reservado" and hist[0]["reason"].startswith("reserva até")


def test_price_change_after_reservation_keeps_snapshot(client, env):
    lot = env["lots"][0]
    res = _reserve(client, env["h"], lot["id"], env["customer"]["id"]).json()
    r = client.patch(
        f"{API}/lots/{lot['id']}",
        headers=env["h"],
        json={"price": "120000.00", "expected_version": 2, "reason": "reajuste"},
    )
    assert r.status_code == 200, r.text
    assert client.get(f"{API}/reservations/{res['id']}", headers=env["h"]).json()["price"] == "99000.90"


def test_second_reservation_of_same_lot_is_rejected(client, env):
    lot_id = env["lots"][0]["id"]
    assert _reserve(client, env["h"], lot_id, env["customer"]["id"]).status_code == 201
    r = _reserve(client, env["h"], lot_id, env["customer"]["id"])
    assert r.status_code == 409
    assert "não está disponível" in r.json()["detail"]


def test_cannot_reserve_sold_or_blocked_lot(client, env):
    h = env["h"]
    lot = env["lots"][1]
    client.post(
        f"{API}/lots/{lot['id']}/status",
        headers=h,
        json={"to_status": "bloqueado", "expected_version": 1, "reason": "vistoria"},
    )
    assert _reserve(client, h, lot["id"], env["customer"]["id"]).status_code == 409


def test_cancel_frees_lot_and_allows_new_reservation(client, env):
    h, lot_id = env["h"], env["lots"][0]["id"]
    res = _reserve(client, h, lot_id, env["customer"]["id"]).json()
    r = client.post(f"{API}/reservations/{res['id']}/cancel", headers=h, json={"reason": "desistência"})
    assert r.status_code == 200 and r.json()["status"] == "cancelada"
    assert r.json()["close_reason"] == "desistência" and r.json()["closed_at"]
    assert client.get(f"{API}/lots/{lot_id}", headers=h).json()["status"] == "disponivel"
    again = client.post(f"{API}/reservations/{res['id']}/cancel", headers=h, json={"reason": "de novo"})
    assert again.status_code == 409
    assert _reserve(client, h, lot_id, env["customer"]["id"]).status_code == 201


def test_convert_sells_lot(client, env):
    h, lot_id = env["h"], env["lots"][0]["id"]
    res = _reserve(client, h, lot_id, env["customer"]["id"]).json()
    r = client.post(f"{API}/reservations/{res['id']}/convert", headers=h, json={"reason": "sinal pago"})
    assert r.status_code == 200 and r.json()["status"] == "convertida"
    assert client.get(f"{API}/lots/{lot_id}", headers=h).json()["status"] == "vendido"
    events = client.get(f"{API}/audit-events", headers=h).json()
    assert any(e["action"] == "reservation.convertida" for e in events)


def test_validity_limits_and_inactive_broker(client, env):
    h = env["h"]
    lot_id = env["lots"][0]["id"]
    assert _reserve(client, h, lot_id, env["customer"]["id"], hours=721).status_code == 422
    client.patch(
        f"{API}/brokers/{env['broker']['id']}", headers=h, json={"is_active": False, "reason": "saiu"}
    )
    r = _reserve(client, h, lot_id, env["customer"]["id"], broker_id=env["broker"]["id"], hours=2)
    assert r.status_code == 409 and "inativo" in r.json()["detail"]


# ─────────────────────────── expiração ───────────────────────────


def test_expired_reservation_releases_lot_on_read(client, env, owner_engine):
    h, lot_id = env["h"], env["lots"][0]["id"]
    res = _reserve(client, h, lot_id, env["customer"]["id"], hours=1).json()
    _expire_in_db(owner_engine, res["id"])

    lot = client.get(f"{API}/lots/{lot_id}", headers=h).json()
    assert lot["status"] == "disponivel" and lot["active_reservation_id"] is None
    got = client.get(f"{API}/reservations/{res['id']}", headers=h).json()
    assert got["status"] == "expirada" and got["close_reason"] == "reserva expirada"
    hist = client.get(f"{API}/lots/{lot_id}/history", headers=h).json()
    assert hist[0]["reason"] == "reserva expirada" and hist[0]["actor_name"] is None
    # cancelar uma reserva expirada não é possível
    r = client.post(f"{API}/reservations/{res['id']}/cancel", headers=h, json={"reason": "tarde"})
    assert r.status_code == 409


def test_new_reservation_replaces_expired_one(client, env, owner_engine):
    h, lot_id = env["h"], env["lots"][0]["id"]
    old = _reserve(client, h, lot_id, env["customer"]["id"]).json()
    _expire_in_db(owner_engine, old["id"])
    r = _reserve(client, h, lot_id, env["customer"]["id"])
    assert r.status_code == 201, r.text
    assert client.get(f"{API}/reservations/{old['id']}", headers=h).json()["status"] == "expirada"


def test_expiration_job_runs_for_all_tenants(client, env, owner_engine):
    res = _reserve(client, env["h"], env["lots"][2]["id"], env["customer"]["id"]).json()
    _expire_in_db(owner_engine, res["id"])
    from scripts.expire_reservations import main

    main()
    with owner_engine.begin() as c:
        st = c.execute(text("SELECT status FROM reservations WHERE id = :i"), {"i": res["id"]}).scalar()
        lot = c.execute(text("SELECT status FROM lots WHERE id = :i"), {"i": env["lots"][2]["id"]}).scalar()
    assert (st, lot) == ("expirada", "disponivel")


def test_cannot_convert_expired_reservation(client, env, owner_engine):
    h = env["h"]
    res = _reserve(client, h, env["lots"][0]["id"], env["customer"]["id"]).json()
    _expire_in_db(owner_engine, res["id"])
    r = client.post(f"{API}/reservations/{res['id']}/convert", headers=h, json={"reason": "tarde"})
    assert r.status_code == 409
    assert client.get(f"{API}/lots/{env['lots'][0]['id']}", headers=h).json()["status"] == "disponivel"


# ─────────────────────────── concorrência ───────────────────────────


def test_concurrent_reservations_only_one_wins(env, world):
    from app.core.db import get_sessionmaker, set_tenant
    from app.services import reservations as svc

    lot_id = uuid.UUID(env["lots"][3]["id"])
    customer_id = uuid.UUID(env["customer"]["id"])
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def reserve(actor: str) -> None:
        db = get_sessionmaker()()
        try:
            set_tenant(db, world.tenant_a)
            barrier.wait()
            svc.create(
                db,
                tenant_id=world.tenant_a,
                lot_id=lot_id,
                customer_id=customer_id,
                broker_id=None,
                hours=24,
                actor=world.users[actor],
                notes=None,
            )
            time.sleep(0.3)
            db.commit()
            outcome = "ok"
        except svc.LotUnavailableError:
            db.rollback()
            outcome = "unavailable"
        finally:
            db.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=reserve, args=(u,)) for u in ("alice", "dave")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert sorted(results) == ["ok", "unavailable"]

    db = get_sessionmaker()()
    set_tenant(db, world.tenant_a)
    with db.begin():
        n = db.execute(
            text("SELECT count(*) FROM reservations WHERE lot_id = :l AND status = 'ativa'"), {"l": lot_id}
        ).scalar()
    db.close()
    assert n == 1


def test_db_unique_index_blocks_double_active_reservation(app_engine, client, env, world):
    """Mesmo que o código falhe, o índice único parcial impede duas reservas ativas no mesmo lote."""
    res = _reserve(client, env["h"], env["lots"][0]["id"], env["customer"]["id"]).json()
    with pytest.raises(IntegrityError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
        c.execute(
            text(
                "INSERT INTO reservations (id, tenant_id, lot_id, customer_id, status, price, expires_at) "
                "VALUES (:i, :t, :l, :c, 'ativa', 1, now() + interval '1 day')"
            ),
            {"i": uuid.uuid4(), "t": world.tenant_a, "l": res["lot_id"], "c": env["customer"]["id"]},
        )


# ─────────────────────────── isolamento ───────────────────────────


def test_tenant_b_cannot_see_or_use_tenant_a_commercial_data(client, env):
    bob = login(client, "bob", "empresa-b")
    res = _reserve(client, env["h"], env["lots"][0]["id"], env["customer"]["id"]).json()
    assert client.get(f"{API}/customers", headers=bob).json() == []
    assert client.get(f"{API}/brokers", headers=bob).json() == []
    assert client.get(f"{API}/reservations", headers=bob).json() == []
    assert client.get(f"{API}/customers/{env['customer']['id']}", headers=bob).status_code == 404
    assert client.get(f"{API}/reservations/{res['id']}", headers=bob).status_code == 404
    assert (
        client.post(
            f"{API}/reservations/{res['id']}/cancel", headers=bob, json={"reason": "invasão"}
        ).status_code
        == 404
    )

    # bob não reserva lote de A, nem usa comprador de A em lote próprio
    assert _reserve(client, bob, env["lots"][1]["id"], env["customer"]["id"]).status_code == 404
    dev_b = client.post(f"{API}/developments", headers=bob, json={"name": "Empreendimento B"}).json()
    blk = client.post(f"{API}/developments/{dev_b['id']}/blocks", headers=bob, json={"code": "B"}).json()
    lot_b = client.post(
        f"{API}/developments/{dev_b['id']}/lots",
        headers=bob,
        json={"block_id": blk["id"], "number": "1", "area_m2": "100", "price": "1"},
    ).json()
    r = _reserve(client, bob, lot_b["id"], env["customer"]["id"])
    assert r.status_code == 404 and "Comprador" in r.json()["detail"]
    customer_b = client.post(f"{API}/customers", headers=bob, json={"name": "Cliente B"}).json()
    r = _reserve(client, bob, lot_b["id"], customer_b["id"], broker_id=env["broker"]["id"])
    assert r.status_code == 404 and "Corretor" in r.json()["detail"]


def test_same_document_allowed_in_different_tenants_but_not_twice_in_one(client, env):
    doc = "529.982.247-25"
    h = env["h"]
    assert (
        client.post(f"{API}/customers", headers=h, json={"name": "Outro Nome", "document": doc}).status_code
        == 409
    )
    bob = login(client, "bob", "empresa-b")
    assert (
        client.post(f"{API}/customers", headers=bob, json={"name": "Mesmo CPF", "document": doc}).status_code
        == 201
    )


def test_rls_hides_phase2a_tables_without_context(app_engine, env):
    with app_engine.begin() as c:
        for table in ("customers", "brokers", "reservations"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608


# ─────────────────────────── RBAC e busca ───────────────────────────


def test_rbac_for_commercial_data(client, env):
    lot_id = env["lots"][0]["id"]
    for user in ("carol", "gil"):  # leitura e corretor (escopo do corretor chega na Fase 2b)
        h = login(client, user)
        assert client.get(f"{API}/customers", headers=h).status_code == 403
        assert client.get(f"{API}/reservations", headers=h).status_code == 403
        assert _reserve(client, h, lot_id, env["customer"]["id"]).status_code == 403
    fabi = login(client, "fabi")  # financeiro: vê, não altera
    assert client.get(f"{API}/reservations", headers=fabi).status_code == 200
    assert client.get(f"{API}/customers", headers=fabi).status_code == 200
    assert _reserve(client, fabi, lot_id, env["customer"]["id"]).status_code == 403
    assert client.post(f"{API}/customers", headers=fabi, json={"name": "Novo"}).status_code == 403
    dave = login(client, "dave")  # comercial: cadastra comprador e reserva; não cadastra corretor
    assert client.post(f"{API}/customers", headers=dave, json={"name": "Cliente do Dave"}).status_code == 201
    assert _reserve(client, dave, lot_id, env["customer"]["id"]).status_code == 201
    assert client.post(f"{API}/brokers", headers=dave, json={"name": "Corretor X"}).status_code == 403


def test_customer_search(client, env):
    h = env["h"]
    client.post(f"{API}/customers", headers=h, json={"name": "Beatriz Exemplo", "email": "bia@example.com"})
    names = lambda q: {c["name"] for c in client.get(f"{API}/customers", headers=h, params={"q": q}).json()}  # noqa: E731
    assert names("beatriz") == {"Beatriz Exemplo"}
    assert names("bia@") == {"Beatriz Exemplo"}
    assert names("529.982") == {"Comprador Fictício"}


def test_reservation_list_filters(client, env):
    h = env["h"]
    a = _reserve(client, h, env["lots"][0]["id"], env["customer"]["id"]).json()
    b = _reserve(client, h, env["lots"][1]["id"], env["customer"]["id"]).json()
    client.post(f"{API}/reservations/{b['id']}/cancel", headers=h, json={"reason": "teste"})
    ativas = client.get(f"{API}/reservations", headers=h, params={"status": "ativa"}).json()
    assert [r["id"] for r in ativas] == [a["id"]]
    assert Decimal(ativas[0]["price"]) == Decimal("99000.90")
    assert len(client.get(f"{API}/reservations", headers=h).json()) == 2
