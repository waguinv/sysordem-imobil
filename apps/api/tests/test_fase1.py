"""Fase 1: empreendimentos, quadras, lotes, proprietários — isolamento, RBAC, status e concorrência."""

import threading
import time
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"


def _dev(client, h, name="Residencial Teste"):
    r = client.post(
        f"{API}/developments",
        headers=h,
        json={"name": name, "kind": "loteamento", "city": "Cidade", "state": "mg"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _block(client, h, dev_id, code="a"):
    r = client.post(f"{API}/developments/{dev_id}/blocks", headers=h, json={"code": code})
    assert r.status_code == 201, r.text
    return r.json()


def _lots(client, h, dev_id, block_id, first=1, last=3, price="150000.50"):
    r = client.post(
        f"{API}/developments/{dev_id}/lots/batch",
        headers=h,
        json={"block_id": block_id, "first": first, "last": last, "area_m2": "300.00", "price": price},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def setup_a(client):
    h = login(client, "alice")
    dev = _dev(client, h)
    block = _block(client, h, dev["id"])
    lots = _lots(client, h, dev["id"], block["id"])
    return {"h": h, "dev": dev, "block": block, "lots": lots}


# ─────────────────────────── cadastro e mapa ───────────────────────────


def test_create_development_blocks_lots_and_map(client, setup_a):
    s = setup_a
    assert s["dev"]["state"] == "MG"
    assert s["block"]["code"] == "A"
    assert [lot["number"] for lot in s["lots"]] == ["01", "02", "03"]
    _block(client, s["h"], s["dev"]["id"], "10")
    _block(client, s["h"], s["dev"]["id"], "2")
    m = client.get(f"{API}/developments/{s['dev']['id']}/map", headers=s["h"]).json()
    assert [b["code"] for b in m["blocks"]] == ["2", "A", "10"]  # ordenação natural
    assert m["development"]["lots_total"] == 3
    assert m["development"]["counts"]["disponivel"] == 3
    assert Decimal(m["development"]["available_value"]) == Decimal("450001.50")


def test_money_is_exact_decimal(client, setup_a):
    lot = setup_a["lots"][0]
    assert lot["price"] == "150000.50"
    r = client.patch(
        f"{API}/lots/{lot['id']}",
        headers=setup_a["h"],
        json={"price": "0.30", "expected_version": 1, "reason": "teste de arredondamento"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["price"] == "0.30"
    bad = client.patch(
        f"{API}/lots/{lot['id']}",
        headers=setup_a["h"],
        json={"price": "10.005", "expected_version": 2, "reason": "três casas"},
    )
    assert bad.status_code == 422


def test_duplicate_block_and_lot_numbers_conflict(client, setup_a):
    s = setup_a
    assert (
        client.post(
            f"{API}/developments/{s['dev']['id']}/blocks", headers=s["h"], json={"code": "A"}
        ).status_code
        == 409
    )
    r = client.post(
        f"{API}/developments/{s['dev']['id']}/lots/batch",
        headers=s["h"],
        json={"block_id": s["block"]["id"], "first": 3, "last": 5, "area_m2": "300", "price": "1"},
    )
    assert r.status_code == 409
    assert "03" in r.json()["detail"]


def test_block_with_lots_cannot_be_deleted(client, setup_a):
    s = setup_a
    url = f"{API}/developments/{s['dev']['id']}/blocks/{s['block']['id']}"
    assert client.delete(url, headers=s["h"]).status_code == 409
    empty = _block(client, s["h"], s["dev"]["id"], "Z")
    assert (
        client.delete(f"{API}/developments/{s['dev']['id']}/blocks/{empty['id']}", headers=s["h"]).status_code
        == 204
    )


# ─────────────────────────── isolamento entre tenants ───────────────────────────


def test_tenant_b_cannot_see_or_touch_tenant_a_data(client, setup_a):
    s = setup_a
    bob = login(client, "bob", "empresa-b")
    dev_id, lot_id = s["dev"]["id"], s["lots"][0]["id"]
    assert client.get(f"{API}/developments", headers=bob).json() == []
    assert client.get(f"{API}/developments/{dev_id}", headers=bob).status_code == 404
    assert client.get(f"{API}/developments/{dev_id}/map", headers=bob).status_code == 404
    assert client.get(f"{API}/lots/{lot_id}", headers=bob).status_code == 404
    assert client.get(f"{API}/lots/{lot_id}/history", headers=bob).status_code == 404
    assert (
        client.post(
            f"{API}/lots/{lot_id}/status",
            headers=bob,
            json={"to_status": "vendido", "expected_version": 1, "reason": "x" * 5},
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"{API}/lots/{lot_id}",
            headers=bob,
            json={"price": "1", "expected_version": 1, "reason": "invasão"},
        ).status_code
        == 404
    )
    assert (
        client.post(f"{API}/developments/{dev_id}/blocks", headers=bob, json={"code": "X"}).status_code == 404
    )


def test_cannot_attach_lot_to_block_of_other_tenant(client, setup_a):
    bob = login(client, "bob", "empresa-b")
    dev_b = _dev(client, bob, "Empreendimento B")
    r = client.post(
        f"{API}/developments/{dev_b['id']}/lots",
        headers=bob,
        json={"block_id": setup_a["block"]["id"], "number": "1", "area_m2": "200", "price": "1"},
    )
    assert r.status_code == 404


def test_composite_fk_blocks_cross_tenant_reference_even_without_rls(app_engine, client, setup_a, world):
    """Checagens de FK ignoram RLS: a FK composta (tenant_id, id) é que impede a referência cruzada."""
    with pytest.raises(IntegrityError), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_b)})
        c.execute(
            text("INSERT INTO blocks (id, tenant_id, development_id, code) VALUES (:i, :t, :d, 'X')"),
            {"i": uuid.uuid4(), "t": world.tenant_b, "d": setup_a["dev"]["id"]},
        )


def test_rls_hides_phase1_tables_without_context(app_engine, setup_a):
    with app_engine.begin() as c:
        for table in ("developments", "blocks", "lots", "lot_status_history", "land_owners"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608


def test_lot_history_is_append_only(app_engine, setup_a, world):
    for stmt in ("UPDATE lot_status_history SET reason = 'x'", "DELETE FROM lot_status_history"):
        with pytest.raises(Exception, match="permission denied"), app_engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
            c.execute(text(stmt))


# ─────────────────────────── RBAC ───────────────────────────


def test_leitura_and_corretor_view_but_cannot_manage(client, setup_a):
    s = setup_a
    for user in ("carol", "gil"):
        h = login(client, user)
        assert client.get(f"{API}/developments/{s['dev']['id']}/map", headers=h).status_code == 200
        assert client.post(f"{API}/developments", headers=h, json={"name": "Novo"}).status_code == 403
        r = client.post(
            f"{API}/lots/{s['lots'][0]['id']}/status",
            headers=h,
            json={"to_status": "vendido", "expected_version": 1, "reason": "venda"},
        )
        assert r.status_code == 403


def test_comercial_changes_status_but_cannot_edit_catalog(client, setup_a):
    s = setup_a
    dave = login(client, "dave")
    lot = s["lots"][0]
    r = client.post(
        f"{API}/lots/{lot['id']}/status",
        headers=dave,
        json={"to_status": "reservado", "expected_version": 1, "reason": "cliente interessado"},
    )
    assert r.status_code == 200, r.text
    assert (
        client.patch(
            f"{API}/lots/{lot['id']}",
            headers=dave,
            json={"price": "1", "expected_version": 2, "reason": "desconto"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{API}/developments/{s['dev']['id']}/blocks", headers=dave, json={"code": "Q"}
        ).status_code
        == 403
    )


def test_land_owner_personal_data_restricted(client):
    alice = login(client, "alice")
    r = client.post(
        f"{API}/land-owners",
        headers=alice,
        json={"name": "Proprietário Fictício", "document": "123.456.789-09"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["document"] == "12345678909"
    assert (
        client.post(
            f"{API}/land-owners", headers=alice, json={"name": "Doc ruim", "document": "123"}
        ).status_code
        == 422
    )
    assert client.get(f"{API}/land-owners", headers=login(client, "fabi")).status_code == 200
    assert (
        client.post(f"{API}/land-owners", headers=login(client, "fabi"), json={"name": "X Y"}).status_code
        == 403
    )
    for user in ("carol", "gil", "dave"):
        assert client.get(f"{API}/land-owners", headers=login(client, user)).status_code == 403
    assert client.get(f"{API}/land-owners", headers=login(client, "bob", "empresa-b")).json() == []


def test_development_cannot_link_owner_of_other_tenant(client):
    alice = login(client, "alice")
    owner = client.post(f"{API}/land-owners", headers=alice, json={"name": "Dono da Área A"}).json()
    bob = login(client, "bob", "empresa-b")
    r = client.post(
        f"{API}/developments", headers=bob, json={"name": "Empreendimento B", "land_owner_id": owner["id"]}
    )
    assert r.status_code == 404


# ─────────────────────────── status, histórico e concorrência ───────────────────────────


def test_status_flow_history_and_audit(client, setup_a):
    s = setup_a
    lot = s["lots"][0]
    url = f"{API}/lots/{lot['id']}/status"
    r = client.post(
        url, headers=s["h"], json={"to_status": "reservado", "expected_version": 1, "reason": "sinal"}
    )
    assert r.json()["status"] == "reservado" and r.json()["version"] == 2
    assert r.json()["allowed_transitions"] == ["disponivel", "vendido"]
    r = client.post(
        url, headers=s["h"], json={"to_status": "vendido", "expected_version": 2, "reason": "contrato"}
    )
    assert r.json()["status"] == "vendido"
    assert r.json()["allowed_transitions"] == []

    # saída de "vendido" só por distrato (Fase 3)
    r = client.post(
        url, headers=s["h"], json={"to_status": "disponivel", "expected_version": 3, "reason": "voltar"}
    )
    assert r.status_code == 422
    # preço de lote vendido é imutável
    r = client.patch(
        f"{API}/lots/{lot['id']}",
        headers=s["h"],
        json={"price": "1.00", "expected_version": 3, "reason": "desconto"},
    )
    assert r.status_code == 409

    hist = client.get(f"{API}/lots/{lot['id']}/history", headers=s["h"]).json()
    assert [(h["from_status"], h["to_status"]) for h in hist] == [
        ("reservado", "vendido"),
        ("disponivel", "reservado"),
        (None, "disponivel"),
    ]
    assert hist[0]["reason"] == "contrato" and hist[0]["actor_name"] == "Alice"
    events = client.get(f"{API}/audit-events", headers=s["h"]).json()
    assert any(e["action"] == "lot.status" and e["data"]["to"] == "vendido" for e in events)


def test_stale_version_is_rejected(client, setup_a):
    lot = setup_a["lots"][1]
    url = f"{API}/lots/{lot['id']}/status"
    h = setup_a["h"]
    assert (
        client.post(
            url, headers=h, json={"to_status": "bloqueado", "expected_version": 1, "reason": "ajuste"}
        ).status_code
        == 200
    )
    r = client.post(url, headers=h, json={"to_status": "vendido", "expected_version": 1, "reason": "venda"})
    assert r.status_code == 409


def test_concurrent_sales_of_same_lot_only_one_wins(setup_a, world):
    """Duas transações simultâneas tentam vender o mesmo lote: o banco serializa e só uma vence."""
    from app.core.db import get_sessionmaker, set_tenant
    from app.services import lots as svc

    lot_id = uuid.UUID(setup_a["lots"][2]["id"])
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def sell(actor: str) -> None:
        db = get_sessionmaker()()
        try:
            set_tenant(db, world.tenant_a)
            barrier.wait()
            svc.change_status(
                db,
                tenant_id=world.tenant_a,
                lot_id=lot_id,
                to_status=svc.LotStatus.VENDIDO,
                expected_version=1,
                actor_user_id=world.users[actor],
                reason=f"venda {actor}",
            )
            time.sleep(0.3)  # segura o lock para a outra transação esperar
            db.commit()
            outcome = "ok"
        except svc.LotConflictError:
            db.rollback()
            outcome = "conflict"
        finally:
            db.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=sell, args=(u,)) for u in ("alice", "erin")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert sorted(results) == ["conflict", "ok"]
    db = get_sessionmaker()()
    set_tenant(db, world.tenant_a)
    with db.begin():
        sold = db.execute(
            text("SELECT count(*) FROM lot_status_history WHERE lot_id = :l AND to_status = 'vendido'"),
            {"l": lot_id},
        ).scalar()
        version = db.execute(text("SELECT version FROM lots WHERE id = :l"), {"l": lot_id}).scalar()
    db.close()
    assert sold == 1
    assert version == 2
