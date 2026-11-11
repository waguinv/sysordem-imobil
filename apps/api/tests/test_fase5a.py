"""Fase 5a: site público — publicação explícita, DTOs públicos sem dados privados, formulário de
contato (consentimento, isca, limites, sem duplicar lead), isolamento e RBAC."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"
PUB = f"{API}/public/sites"

SITE = {
    "published": True,
    "title": "Loteadora A",
    "tagline": "Lotes prontos para construir",
    "about": "Sobre nós",
    "contact_phone": "(11) 4000-0000",
    "contact_whatsapp": "11999990000",
    "contact_email": "contato@empresa-a.example",
    "show_prices": True,
    "lead_form_enabled": True,
}

PUBLIC_SITE_KEYS = {
    "slug",
    "name",
    "tagline",
    "about",
    "contact",
    "show_prices",
    "lead_form_enabled",
    "developments",
}
PUBLIC_DEV_KEYS = {
    "id",
    "name",
    "kind",
    "city",
    "state",
    "description",
    "lots_total",
    "lots_available",
    "price_from",
}
PUBLIC_LOT_KEYS = {"id", "number", "area_m2", "front_m", "available", "price"}


def _today():
    from app.core.clock import business_today

    return business_today()


@pytest.fixture()
def env(client):
    h = login(client, "alice")
    dev = client.post(
        f"{API}/developments",
        headers=h,
        json={
            "name": "Residencial Vitrine",
            "city": "Campinas",
            "state": "SP",
            "description": "NOTA INTERNA X",
        },
    ).json()
    hidden = client.post(f"{API}/developments", headers=h, json={"name": "Projeto Sigiloso"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "A"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 4, "area_m2": "300", "price": "100000.00"},
    ).json()
    # lote 1 reservado, lote 2 vendido (contrato assinado) — nada disso pode vazar
    customer = client.post(
        f"{API}/customers", headers=h, json={"name": "Comprador Secreto", "document": "52998224725"}
    ).json()
    client.post(
        f"{API}/reservations", headers=h, json={"lot_id": lots[0]["id"], "customer_id": customer["id"]}
    )
    res = client.post(
        f"{API}/reservations", headers=h, json={"lot_id": lots[1]["id"], "customer_id": customer["id"]}
    ).json()
    c = client.post(
        f"{API}/contracts",
        headers=h,
        json={
            "reservation_id": res["id"],
            "down_payment": "10000.00",
            "installments_count": 10,
            "interest_monthly_pct": "1",
            "first_due_date": (_today() + timedelta(days=30)).isoformat(),
        },
    ).json()
    client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1})
    return {"h": h, "dev": dev, "hidden": hidden, "lots": lots, "contract": c}


def _publish(client, h, dev_id, description="Lotes de 300 m² perto do centro.", published=True):
    return client.patch(
        f"{API}/developments/{dev_id}/site",
        headers=h,
        json={"published": published, "description": description},
    )


def _go_live(client, env, **site):
    r = client.put(f"{API}/site/settings", headers=env["h"], json={**SITE, **site})
    assert r.status_code == 200, r.text
    assert _publish(client, env["h"], env["dev"]["id"]).status_code == 200


def _lead(client, **body):
    base = {"name": "Interessado", "phone": "(11) 98888-7777", "consent": True}
    return client.post(f"{PUB}/empresa-a/leads", json={**base, **body})


# ─────────────────────────── publicação ───────────────────────────


def test_nothing_is_public_until_published(client, env):
    h = env["h"]
    assert client.get(f"{PUB}/empresa-a").status_code == 404
    assert client.get(f"{PUB}/nao-existe").status_code == 404
    s = client.get(f"{API}/site/settings", headers=h).json()
    assert s["published"] is False and s["slug"] == "empresa-a" and s["updated_at"] is None

    # site publicado, mas nenhum empreendimento publicado
    client.put(f"{API}/site/settings", headers=h, json=SITE)
    site = client.get(f"{PUB}/empresa-a").json()
    assert site["developments"] == [] and site["name"] == "Loteadora A"

    # publicar exige texto público
    r = _publish(client, h, env["dev"]["id"], description="  ")
    assert r.status_code == 422 and "texto público" in r.json()["detail"]
    assert _publish(client, h, env["dev"]["id"]).json()["site_published"] is True
    site = client.get(f"{PUB}/empresa-a").json()
    assert [d["name"] for d in site["developments"]] == ["Residencial Vitrine"]
    assert client.get(f"{PUB}/empresa-a/developments/{env['hidden']['id']}").status_code == 404

    # despublicar some na hora
    _publish(client, h, env["dev"]["id"], published=False)
    assert client.get(f"{PUB}/empresa-a/developments/{env['dev']['id']}").status_code == 404
    # site despublicado = 404 para tudo
    client.put(f"{API}/site/settings", headers=h, json={**SITE, "published": False})
    assert client.get(f"{PUB}/empresa-a").status_code == 404
    actions = {a["action"] for a in client.get(f"{API}/audit-events", headers=h).json()}
    assert {"site.settings", "site.publish", "site.unpublish"} <= actions


def test_public_dtos_expose_only_allowed_fields(client, env):
    _go_live(client, env)
    raw = client.get(f"{PUB}/empresa-a")
    assert raw.headers["cache-control"] == "no-store"
    site = raw.json()
    assert set(site) == PUBLIC_SITE_KEYS and set(site["contact"]) == {"phone", "whatsapp", "email"}
    [summary] = site["developments"]
    assert set(summary) == PUBLIC_DEV_KEYS
    assert (summary["lots_total"], summary["lots_available"], summary["price_from"]) == (4, 2, "100000.00")
    assert summary["description"] == "Lotes de 300 m² perto do centro."

    detail = client.get(f"{PUB}/empresa-a/developments/{env['dev']['id']}")
    d = detail.json()
    assert set(d) == PUBLIC_DEV_KEYS | {"blocks"}
    lots = d["blocks"][0]["lots"]
    assert all(set(lot) == PUBLIC_LOT_KEYS for lot in lots)
    assert [(lot["number"], lot["available"], lot["price"]) for lot in lots] == [
        ("01", False, None),  # reservado → só "indisponível", sem preço
        ("02", False, None),  # vendido → idem
        ("03", True, "100000.00"),
        ("04", True, "100000.00"),
    ]
    body = raw.text + detail.text
    for secret in (
        "Comprador Secreto",
        "52998224725",
        env["contract"]["number"],
        "NOTA INTERNA X",
        "Projeto Sigiloso",
        "reservado",
        "vendido",
    ):
        assert secret not in body


def test_hide_prices(client, env):
    _go_live(client, env, show_prices=False)
    site = client.get(f"{PUB}/empresa-a").json()
    assert site["developments"][0]["price_from"] is None
    d = client.get(f"{PUB}/empresa-a/developments/{env['dev']['id']}").json()
    assert all(lot["price"] is None for b in d["blocks"] for lot in b["lots"])


# ─────────────────────────── formulário de contato ───────────────────────────


def _leads(owner_engine, world):
    with owner_engine.begin() as c:
        return c.execute(
            text(
                "SELECT id, name, phone, email, source, development_id, consent_at, created_by "
                "FROM leads WHERE tenant_id = :t ORDER BY created_at"
            ),
            {"t": world.tenant_a},
        ).all()


def _activities(owner_engine, lead_id):
    with owner_engine.begin() as c:
        return [
            r[0]
            for r in c.execute(
                text("SELECT description FROM lead_activities WHERE lead_id = :l ORDER BY seq"),
                {"l": lead_id},
            )
        ]


def test_lead_from_site(client, env, owner_engine, world):
    _go_live(client, env)
    lot = env["lots"][2]
    r = _lead(client, development_id=env["dev"]["id"], lot_id=lot["id"], message="Quero visitar")
    assert r.status_code == 202 and r.json() == {"status": "recebido"}
    [lead] = _leads(owner_engine, world)
    assert lead.source == "site" and str(lead.development_id) == env["dev"]["id"]
    assert lead.consent_at is not None and lead.created_by is None
    [act] = _activities(owner_engine, lead.id)
    assert "Residencial Vitrine" in act and "Quadra A · Lote 03" in act and "Quero visitar" in act
    assert "Consentimento LGPD" in act

    # mesmo telefone com outra formatação: mesma pessoa, nova anotação (sem lead duplicado)
    assert _lead(client, phone="11 98888 7777", message="Ainda tenho interesse").status_code == 202
    assert len(_leads(owner_engine, world)) == 1
    assert len(_activities(owner_engine, lead.id)) == 2

    # aparece na lista de leads do painel
    leads = client.get(f"{API}/leads", headers=env["h"]).json()
    assert [x["source"] for x in leads] == ["site"]


def test_lead_validation_and_abuse(client, env, owner_engine, world):
    _go_live(client, env)
    assert _lead(client, consent=False).status_code == 422  # sem consentimento
    assert _lead(client, phone=None).status_code == 422  # sem contato
    assert _lead(client, phone="123").status_code == 422  # telefone sem DDD
    other_lot = env["lots"][0]["id"]
    assert _lead(client, lot_id=other_lot).status_code == 404  # lote sem empreendimento
    assert _lead(client, development_id=env["hidden"]["id"]).status_code == 404  # não publicado
    # robô preencheu o campo-isca: 202, mas nada gravado
    assert _lead(client, website="http://spam.example").status_code == 202
    assert _leads(owner_engine, world) == []

    for _ in range(3):
        assert _lead(client, email="mesmo@example.com", phone=None).status_code == 202
    r = _lead(client, email="MESMO@example.com", phone=None)
    assert r.status_code == 429  # limite por contato

    client.put(f"{API}/site/settings", headers=env["h"], json={**SITE, "lead_form_enabled": False})
    assert _lead(client, email="outro@example.com", phone=None).status_code == 404


def test_tenant_limit(client, env, monkeypatch):
    from app.core.config import get_settings

    _go_live(client, env)
    monkeypatch.setattr(get_settings(), "public_leads_per_tenant_per_hour", 2)
    assert _lead(client, email="a@example.com", phone=None).status_code == 202
    assert _lead(client, email="b@example.com", phone=None).status_code == 202
    assert _lead(client, email="c@example.com", phone=None).status_code == 429


# ─────────────────────────── isolamento e RBAC ───────────────────────────


def test_tenant_isolation(client, env, owner_engine, world):
    _go_live(client, env)
    hb = login(client, "bob", "empresa-b")
    client.put(f"{API}/site/settings", headers=hb, json={**SITE, "title": "Loteadora B"})
    # empreendimento da A pelo endereço da B: não existe
    assert client.get(f"{PUB}/empresa-b/developments/{env['dev']['id']}").status_code == 404
    assert client.get(f"{PUB}/empresa-b").json()["developments"] == []
    assert (
        client.post(
            f"{PUB}/empresa-b/leads",
            json={
                "name": "Xavier",
                "phone": "11988887777",
                "consent": True,
                "development_id": env["dev"]["id"],
            },
        ).status_code
        == 404
    )
    # B não publica nem enxerga empreendimento da A
    assert _publish(client, hb, env["dev"]["id"]).status_code == 404
    assert client.get(f"{API}/site/developments", headers=hb).json() == []
    # empresa desativada some do ar
    with owner_engine.begin() as c:
        c.execute(text("UPDATE tenants SET is_active = false WHERE id = :t"), {"t": world.tenant_a})
    assert client.get(f"{PUB}/empresa-a").status_code == 404


def test_rbac(client, env):
    for user in ("dave", "fabi", "gil", "carol"):  # comercial, financeiro, corretor, leitura
        hh = login(client, user)
        assert client.get(f"{API}/site/settings", headers=hh).status_code == 403
        assert client.put(f"{API}/site/settings", headers=hh, json=SITE).status_code == 403
        assert _publish(client, hh, env["dev"]["id"]).status_code == 403
    erin = login(client, "erin")  # admin
    assert client.put(f"{API}/site/settings", headers=erin, json=SITE).status_code == 200


def test_public_payload_is_json_serializable_without_internal_fields(client, env):
    _go_live(client, env)
    d = client.get(f"{PUB}/empresa-a/developments/{env['dev']['id']}").json()
    flat = json.dumps(d)
    for internal in ("tenant_id", "owner_share_pct", "land_owner", "notes", "version", "geometry", "status"):
        assert internal not in flat
