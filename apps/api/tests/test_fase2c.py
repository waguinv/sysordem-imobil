"""Fase 2c: leads (CRM), propostas e escopo do corretor — regras, concorrência, isolamento e RBAC."""

import threading
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"


def _future(days: int = 30) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


@pytest.fixture()
def env(client):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial CRM"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "B"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 4, "area_m2": "300", "price": "100000.00"},
    ).json()
    customer = client.post(f"{API}/customers", headers=h, json={"name": "Cliente da Empresa"}).json()
    other = client.post(f"{API}/customers", headers=h, json={"name": "Outro Cliente"}).json()
    broker = client.post(f"{API}/brokers", headers=h, json={"name": "Corretor Casa"}).json()
    return {"h": h, "dev": dev, "lots": lots, "customer": customer, "other": other, "broker": broker}


def _lead(client, h, **extra):
    body = {"name": "Interessado Fictício", "phone": "11 99999-0000", "source": "site", **extra}
    return client.post(f"{API}/leads", headers=h, json=body)


def _proposal(client, h, lot_id, customer_id, **extra):
    body = {
        "lot_id": lot_id,
        "customer_id": customer_id,
        "total_price": "95000.00",
        "down_payment": "15000.00",
        "installments_count": 80,
        "first_due_date": _future(),
        **extra,
    }
    return client.post(f"{API}/proposals", headers=h, json=body)


def _link_gil(client, env, world):
    """Liga o usuário corretor `gil` a um cadastro de corretor (exigido pelo escopo)."""
    r = client.post(
        f"{API}/brokers",
        headers=env["h"],
        json={"name": "Gil Corretor", "user_id": str(world.users["gil"])},
    )
    assert r.status_code == 201, r.text
    return r.json(), login(client, "gil")


# ─────────────────────────── leads ───────────────────────────


def test_lead_requires_contact_and_starts_new(client, env):
    h = env["h"]
    assert client.post(f"{API}/leads", headers=h, json={"name": "Sem Contato"}).status_code == 422
    r = _lead(client, h, development_id=env["dev"]["id"], broker_id=env["broker"]["id"])
    assert r.status_code == 201, r.text
    lead = r.json()
    assert lead["status"] == "novo"
    assert lead["development_name"] == "Residencial CRM" and lead["broker_name"] == "Corretor Casa"
    assert [a["description"] for a in lead["activities"]] == ["Lead cadastrado"]


def test_contact_activity_moves_new_lead_to_in_contact(client, env):
    h = env["h"]
    lead = _lead(client, h).json()
    r = client.post(
        f"{API}/leads/{lead['id']}/activities", headers=h, json={"kind": "nota", "description": "ok"}
    )
    assert r.json()["status"] == "novo"  # nota não conta como contato
    r = client.post(
        f"{API}/leads/{lead['id']}/activities",
        headers=h,
        json={"kind": "ligacao", "description": "Ligou pedindo tabela"},
    )
    assert r.status_code == 201
    detail = r.json()
    assert detail["status"] == "em_contato"
    assert detail["activities"][0]["description"] == "Novo → Em contato"
    assert detail["activities"][0]["author_name"] == "Alice"


def test_lost_lead_requires_reason_and_cannot_convert_until_reopened(client, env):
    h = env["h"]
    lead = _lead(client, h).json()
    url = f"{API}/leads/{lead['id']}"
    assert client.patch(url, headers=h, json={"status": "perdido"}).status_code == 422
    r = client.patch(url, headers=h, json={"status": "perdido", "lost_reason": "comprou em outro lugar"})
    assert r.json()["status"] == "perdido" and r.json()["lost_reason"] == "comprou em outro lugar"
    assert client.post(f"{url}/convert", headers=h, json={}).status_code == 409
    r = client.patch(url, headers=h, json={"status": "qualificado"})
    assert r.json()["status"] == "qualificado" and r.json()["lost_reason"] is None
    assert client.post(f"{url}/convert", headers=h, json={}).status_code == 200


def test_convert_creates_customer_once(client, env):
    h = env["h"]
    lead = _lead(client, h, email="lead@example.com", broker_id=env["broker"]["id"]).json()
    r = client.post(
        f"{API}/leads/{lead['id']}/convert", headers=h, json={"document": "529.982.247-25", "state": "sp"}
    )
    assert r.status_code == 200, r.text
    converted = r.json()
    assert converted["status"] == "convertido" and converted["customer_id"]
    c = client.get(f"{API}/customers/{converted['customer_id']}", headers=h).json()
    assert c["name"] == "Interessado Fictício" and c["email"] == "lead@example.com"
    assert c["document"] == "52998224725" and c["state"] == "SP"
    assert c["broker_id"] == env["broker"]["id"]  # responsável herdado do lead
    assert client.post(f"{API}/leads/{lead['id']}/convert", headers=h, json={}).status_code == 409
    # convertido: dados de contato passam a ser do comprador
    assert client.patch(f"{API}/leads/{lead['id']}", headers=h, json={"name": "X Y"}).status_code == 409

    # mesmo CPF em outro lead → orienta a ligar ao comprador existente
    lead2 = _lead(client, h).json()
    r = client.post(f"{API}/leads/{lead2['id']}/convert", headers=h, json={"document": "52998224725"})
    assert r.status_code == 409
    r = client.post(
        f"{API}/leads/{lead2['id']}/convert", headers=h, json={"customer_id": converted["customer_id"]}
    )
    assert r.status_code == 200 and r.json()["customer_id"] == converted["customer_id"]


def test_lead_search_and_filters(client, env):
    h = env["h"]
    a = _lead(client, h, name="Ana Interessada").json()
    _lead(client, h, name="Bruno Curioso")
    client.patch(f"{API}/leads/{a['id']}", headers=h, json={"status": "qualificado"})
    names = lambda **p: {x["name"] for x in client.get(f"{API}/leads", headers=h, params=p).json()}  # noqa: E731
    assert names(q="ana") == {"Ana Interessada"}
    assert names(status="qualificado") == {"Ana Interessada"}
    assert names() == {"Ana Interessada", "Bruno Curioso"}


# ─────────────────────────── propostas ───────────────────────────


def test_create_proposal_uses_policy_and_reports_discount(client, env):
    h = env["h"]
    r = _proposal(client, h, env["lots"][0]["id"], env["customer"]["id"], broker_id=env["broker"]["id"])
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["status"] == "aberta"
    assert p["list_price"] == "100000.00" and p["total_price"] == "95000.00"
    assert Decimal(p["discount_pct"]) == Decimal("5.00")
    assert p["amortization"] == "price" and Decimal(p["interest_monthly_pct"]) == 0
    assert p["installment_amount"] == "1000.00"  # 80.000 / 80 sem juros
    assert date.fromisoformat(p["valid_until"]) == datetime.now(UTC).date() + timedelta(days=7)
    assert p["broker_name"] == "Corretor Casa" and p["lot_number"] == "01"
    # lote continua disponível enquanto a proposta está aberta
    assert client.get(f"{API}/lots/{p['lot_id']}", headers=h).json()["status"] == "disponivel"


@pytest.mark.parametrize(
    ("over", "msg"),
    [
        ({"installments_count": 300}, "parcelas acima"),
        ({"interest_monthly_pct": "3"}, "Juros acima"),
        ({"down_payment": "96000.00"}, "Entrada não pode"),
    ],
)
def test_proposal_outside_policy_is_rejected(client, env, over, msg):
    r = _proposal(client, env["h"], env["lots"][0]["id"], env["customer"]["id"], **over)
    assert r.status_code == 409 and msg in r.json()["detail"]


def test_proposal_validity_limits(client, env):
    h, lot, cust = env["h"], env["lots"][0]["id"], env["customer"]["id"]
    yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    assert _proposal(client, h, lot, cust, valid_until=yesterday).status_code == 422
    assert _proposal(client, h, lot, cust, valid_until=_future(91)).status_code == 422
    assert _proposal(client, h, lot, cust, valid_until=_future(90)).status_code == 201


def test_accept_reserves_lot_and_links_reservation(client, env):
    h = env["h"]
    p = _proposal(
        client, h, env["lots"][0]["id"], env["customer"]["id"], broker_id=env["broker"]["id"]
    ).json()
    r = client.post(f"{API}/proposals/{p['id']}/accept", headers=h, json={"hours": 48})
    assert r.status_code == 200, r.text
    accepted = r.json()
    assert accepted["status"] == "aceita" and accepted["reservation_id"] and accepted["decided_at"]
    res = client.get(f"{API}/reservations/{accepted['reservation_id']}", headers=h).json()
    assert res["status"] == "ativa" and res["customer_id"] == env["customer"]["id"]
    assert res["price"] == "95000.00"  # a reserva congela o valor negociado, não o de tabela
    assert res["broker_id"] == env["broker"]["id"]
    assert client.get(f"{API}/lots/{p['lot_id']}", headers=h).json()["status"] == "reservado"
    # o contrato encontra a proposta pela reserva (para pré-preencher as condições)
    found = client.get(f"{API}/proposals", headers=h, params={"reservation_id": res["id"]}).json()
    assert [x["id"] for x in found] == [p["id"]]
    # decisão é final
    assert (
        client.post(f"{API}/proposals/{p['id']}/cancel", headers=h, json={"reason": "xyz"}).status_code == 409
    )


def test_competing_proposal_cannot_be_accepted_after_lot_reserved(client, env):
    h, lot = env["h"], env["lots"][0]["id"]
    mine = _proposal(client, h, lot, env["customer"]["id"]).json()
    theirs = _proposal(client, h, lot, env["other"]["id"]).json()
    assert client.post(f"{API}/proposals/{mine['id']}/accept", headers=h, json={}).status_code == 200
    r = client.post(f"{API}/proposals/{theirs['id']}/accept", headers=h, json={})
    assert r.status_code == 409 and "outro cliente" in r.json()["detail"]
    # e não dá para propor sobre lote reservado a outro cliente
    r = _proposal(client, h, lot, env["other"]["id"])
    assert r.status_code == 409 and "outro cliente" in r.json()["detail"]


def test_accept_uses_existing_reservation_of_same_customer(client, env):
    h, lot = env["h"], env["lots"][1]["id"]
    res = client.post(
        f"{API}/reservations", headers=h, json={"lot_id": lot, "customer_id": env["customer"]["id"]}
    ).json()
    p = _proposal(client, h, lot, env["customer"]["id"]).json()  # negociação sobre a própria reserva
    accepted = client.post(f"{API}/proposals/{p['id']}/accept", headers=h, json={}).json()
    assert accepted["reservation_id"] == res["id"]
    # segunda proposta aceita não pode reaproveitar a mesma reserva
    p2 = _proposal(client, h, lot, env["customer"]["id"]).json()
    r = client.post(f"{API}/proposals/{p2['id']}/accept", headers=h, json={})
    assert r.status_code == 409


def test_cannot_propose_for_sold_or_blocked_lot(client, env):
    h, lot = env["h"], env["lots"][2]
    detail = client.get(f"{API}/lots/{lot['id']}", headers=h).json()
    client.post(
        f"{API}/lots/{lot['id']}/status",
        headers=h,
        json={"to_status": "bloqueado", "expected_version": detail["version"], "reason": "pendência"},
    )
    r = _proposal(client, h, lot["id"], env["customer"]["id"])
    assert r.status_code == 409 and "não está à venda" in r.json()["detail"]


def test_reject_and_cancel_close_proposal(client, env):
    h, lot = env["h"], env["lots"][0]["id"]
    a = _proposal(client, h, lot, env["customer"]["id"]).json()
    b = _proposal(client, h, lot, env["customer"]["id"]).json()
    assert (
        client.post(f"{API}/proposals/{a['id']}/reject", headers=h, json={"reason": "x"}).status_code == 422
    )
    r = client.post(f"{API}/proposals/{a['id']}/reject", headers=h, json={"reason": "valor baixo"})
    assert r.json()["status"] == "recusada" and r.json()["decision_reason"] == "valor baixo"
    r = client.post(f"{API}/proposals/{b['id']}/cancel", headers=h, json={"reason": "cliente desistiu"})
    assert r.json()["status"] == "cancelada"
    assert client.post(f"{API}/proposals/{a['id']}/accept", headers=h, json={}).status_code == 409
    assert client.get(f"{API}/lots/{lot}", headers=h).json()["status"] == "disponivel"


def test_expired_proposal_cannot_be_accepted(client, env, owner_engine):
    h = env["h"]
    p = _proposal(client, h, env["lots"][0]["id"], env["customer"]["id"]).json()
    with owner_engine.begin() as c:
        c.execute(text("UPDATE proposals SET valid_until = current_date - 1 WHERE id = :i"), {"i": p["id"]})
    r = client.post(f"{API}/proposals/{p['id']}/accept", headers=h, json={})
    assert r.status_code == 409
    assert client.get(f"{API}/proposals/{p['id']}", headers=h).json()["status"] == "expirada"
    assert client.get(f"{API}/lots/{p['lot_id']}", headers=h).json()["status"] == "disponivel"


def test_proposal_from_lead_records_activity(client, env):
    h = env["h"]
    lead = _lead(client, h).json()
    assert (
        _proposal(client, h, env["lots"][0]["id"], env["customer"]["id"], lead_id=lead["id"]).status_code
        == 409
    )
    customer_id = client.post(f"{API}/leads/{lead['id']}/convert", headers=h, json={}).json()["customer_id"]
    p = _proposal(client, h, env["lots"][0]["id"], customer_id, lead_id=lead["id"]).json()
    client.post(f"{API}/proposals/{p['id']}/accept", headers=h, json={})
    acts = [a["description"] for a in client.get(f"{API}/leads/{lead['id']}", headers=h).json()["activities"]]
    assert acts[0] == "Proposta aceita — lote reservado"
    assert acts[1].startswith("Proposta registrada: 95000.00")
    assert [
        x["id"] for x in client.get(f"{API}/proposals", headers=h, params={"lead_id": lead["id"]}).json()
    ] == [p["id"]]


def test_concurrent_accepts_for_same_lot_only_one_wins(client, env, world):
    from app.core.db import get_sessionmaker, set_tenant
    from app.services import proposals as svc

    h, lot = env["h"], env["lots"][3]["id"]
    ids = [
        uuid.UUID(_proposal(client, h, lot, env["customer"]["id"]).json()["id"]),
        uuid.UUID(_proposal(client, h, lot, env["other"]["id"]).json()["id"]),
    ]
    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def accept(pid: uuid.UUID) -> None:
        db = get_sessionmaker()()
        try:
            set_tenant(db, world.tenant_a)
            barrier.wait()
            svc.accept(
                db,
                tenant_id=world.tenant_a,
                proposal_id=pid,
                hours=24,
                actor=world.users["alice"],
                reason="ok",
            )
            time.sleep(0.3)
            db.commit()
            outcome = "ok"
        except svc.ProposalError:
            db.rollback()
            outcome = "rejected"
        finally:
            db.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=accept, args=(pid,)) for pid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert sorted(results) == ["ok", "rejected"]
    active = client.get(f"{API}/reservations", headers=h, params={"status": "ativa"}).json()
    assert len([r for r in active if r["lot_id"] == lot]) == 1


# ─────────────────────────── escopo do corretor ───────────────────────────


def test_broker_user_link_validation(client, env, world):
    h = env["h"]
    stranger = str(world.users["bob"])  # membro só da empresa B
    r = client.post(f"{API}/brokers", headers=h, json={"name": "Externo", "user_id": stranger})
    assert r.status_code == 422
    _link_gil(client, env, world)
    r = client.patch(
        f"{API}/brokers/{env['broker']['id']}",
        headers=h,
        json={"user_id": str(world.users["gil"]), "reason": "vincular"},
    )
    assert r.status_code == 409  # já ligado a outro corretor


def test_unlinked_broker_user_gets_clear_403(client, env):
    gil = login(client, "gil")
    r = client.get(f"{API}/leads", headers=gil)
    assert r.status_code == 403 and "corretor" in r.json()["detail"]


def test_broker_sees_and_acts_only_on_own_records(client, env, world):
    h = env["h"]
    gil_broker, gil = _link_gil(client, env, world)

    # dados da empresa/de outro corretor
    _lead(client, h, name="Lead da Casa", broker_id=env["broker"]["id"])
    house_res = client.post(
        f"{API}/reservations",
        headers=h,
        json={
            "lot_id": env["lots"][0]["id"],
            "customer_id": env["customer"]["id"],
            "broker_id": env["broker"]["id"],
        },
    ).json()

    # lead do corretor: broker forçado para ele; não pode criar em nome de outro
    assert _lead(client, gil, broker_id=env["broker"]["id"]).status_code == 403
    mine = _lead(client, gil, name="Lead do Gil").json()
    assert mine["broker_id"] == gil_broker["id"]
    assert {x["name"] for x in client.get(f"{API}/leads", headers=gil).json()} == {"Lead do Gil"}
    assert len(client.get(f"{API}/leads", headers=h).json()) == 2  # gestão vê todos
    assert (
        client.patch(
            f"{API}/leads/{mine['id']}", headers=gil, json={"broker_id": env["broker"]["id"]}
        ).status_code
        == 403
    )

    # compradores: só os dele
    assert client.get(f"{API}/customers", headers=gil).json() == []
    assert client.get(f"{API}/customers/{env['customer']['id']}", headers=gil).status_code == 404
    customer_id = client.post(f"{API}/leads/{mine['id']}/convert", headers=gil, json={}).json()["customer_id"]
    own = client.post(f"{API}/customers", headers=gil, json={"name": "Cliente Direto do Gil"}).json()
    assert own["broker_id"] == gil_broker["id"]
    assert {c["id"] for c in client.get(f"{API}/customers", headers=gil).json()} == {customer_id, own["id"]}
    r = client.patch(
        f"{API}/customers/{own['id']}",
        headers=gil,
        json={"broker_id": env["broker"]["id"], "reason": "trocar"},
    )
    assert r.status_code == 403

    # reservas: só as dele; não reserva para cliente alheio
    assert client.get(f"{API}/reservations", headers=gil).json() == []
    assert client.get(f"{API}/reservations/{house_res['id']}", headers=gil).status_code == 404
    assert (
        client.post(
            f"{API}/reservations/{house_res['id']}/cancel", headers=gil, json={"reason": "abc"}
        ).status_code
        == 404
    )
    r = client.post(
        f"{API}/reservations",
        headers=gil,
        json={"lot_id": env["lots"][1]["id"], "customer_id": env["other"]["id"]},
    )
    assert r.status_code == 404
    r = client.post(
        f"{API}/reservations", headers=gil, json={"lot_id": env["lots"][1]["id"], "customer_id": customer_id}
    )
    assert r.status_code == 201 and r.json()["broker_id"] == gil_broker["id"]
    assert [x["customer_id"] for x in client.get(f"{API}/reservations", headers=gil).json()] == [customer_id]

    # propostas: só as dele, em nome dele, para clientes dele
    assert _proposal(client, gil, env["lots"][2]["id"], env["other"]["id"]).status_code == 404
    p = _proposal(client, gil, env["lots"][2]["id"], own["id"]).json()
    assert p["broker_id"] == gil_broker["id"]
    house_p = _proposal(client, h, env["lots"][3]["id"], env["customer"]["id"]).json()
    assert [x["id"] for x in client.get(f"{API}/proposals", headers=gil).json()] == [p["id"]]
    assert client.get(f"{API}/proposals/{house_p['id']}", headers=gil).status_code == 404
    assert client.post(f"{API}/proposals/{house_p['id']}/accept", headers=gil, json={}).status_code == 404
    assert client.post(f"{API}/proposals/{p['id']}/accept", headers=gil, json={}).status_code == 200

    # sem acesso a contratos nem ao cadastro de corretores
    assert client.get(f"{API}/contracts", headers=gil).status_code == 403
    assert client.get(f"{API}/brokers", headers=gil).status_code == 403


def test_inactive_broker_loses_access(client, env, world):
    gil_broker, gil = _link_gil(client, env, world)
    client.patch(
        f"{API}/brokers/{gil_broker['id']}",
        headers=env["h"],
        json={"is_active": False, "reason": "desligado"},
    )
    assert client.get(f"{API}/leads", headers=gil).status_code == 403


# ─────────────────────────── isolamento e RBAC ───────────────────────────


def test_tenant_b_cannot_see_or_use_crm_data(client, env):
    h = env["h"]
    lead = _lead(client, h).json()
    p = _proposal(client, h, env["lots"][0]["id"], env["customer"]["id"]).json()
    bob = login(client, "bob", "empresa-b")
    assert client.get(f"{API}/leads", headers=bob).json() == []
    assert client.get(f"{API}/proposals", headers=bob).json() == []
    assert client.get(f"{API}/leads/{lead['id']}", headers=bob).status_code == 404
    assert client.get(f"{API}/proposals/{p['id']}", headers=bob).status_code == 404
    assert client.post(f"{API}/proposals/{p['id']}/accept", headers=bob, json={}).status_code == 404
    assert client.post(f"{API}/leads/{lead['id']}/convert", headers=bob, json={}).status_code == 404
    # B não usa lote/comprador de A numa proposta
    assert _proposal(client, bob, env["lots"][0]["id"], env["customer"]["id"]).status_code == 404


def test_rls_hides_crm_tables_without_context(app_engine, client, env):
    _lead(client, env["h"])
    _proposal(client, env["h"], env["lots"][0]["id"], env["customer"]["id"])
    with app_engine.begin() as c:
        for table in ("leads", "lead_activities", "proposals"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608


def test_lead_activities_are_append_only(app_engine, client, env, world):
    _lead(client, env["h"])
    with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
        c.execute(text("UPDATE lead_activities SET description = 'adulterado'"))


def test_rbac_for_crm(client, env):
    lot, cust = env["lots"][0]["id"], env["customer"]["id"]
    carol = login(client, "carol")  # leitura
    assert client.get(f"{API}/leads", headers=carol).status_code == 403
    assert client.get(f"{API}/proposals", headers=carol).status_code == 403
    fabi = login(client, "fabi")  # financeiro: consulta propostas, não vê leads nem altera
    assert client.get(f"{API}/proposals", headers=fabi).status_code == 200
    assert client.get(f"{API}/leads", headers=fabi).status_code == 403
    assert _proposal(client, fabi, lot, cust).status_code == 403
    dave = login(client, "dave")  # comercial: tudo do CRM
    lead = _lead(client, dave).json()
    assert client.post(f"{API}/leads/{lead['id']}/convert", headers=dave, json={}).status_code == 200
    p = _proposal(client, dave, lot, cust).json()
    assert client.post(f"{API}/proposals/{p['id']}/accept", headers=dave, json={}).status_code == 200


def test_broker_reads_contract_policy_for_proposals(client, env, world):
    _, gil = _link_gil(client, env, world)
    assert client.get(f"{API}/settings/contract-policy", headers=gil).status_code == 200
    carol = login(client, "carol")
    assert client.get(f"{API}/settings/contract-policy", headers=carol).status_code == 403
