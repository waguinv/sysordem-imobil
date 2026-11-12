"""Fase 5b: portal do comprador — convite, ativação, login, escopo, 2ª via, informe e isolamento."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"
PW = "senha-do-comprador-1"


def _today():
    from app.core.clock import business_today

    return business_today()


@pytest.fixture()
def env(client):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Loteamento Portal"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "P"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 4, "area_m2": "300", "price": "100000.00"},
    ).json()
    buyer = client.post(
        f"{API}/customers", headers=h, json={"name": "Comprador Portal", "email": "Comprador@Example.com"}
    ).json()
    other = client.post(f"{API}/customers", headers=h, json={"name": "Outro Comprador"}).json()

    def contract(lot_idx: int, customer_id: str, sign: bool = True):
        res = client.post(
            f"{API}/reservations", headers=h, json={"lot_id": lots[lot_idx]["id"], "customer_id": customer_id}
        ).json()
        c = client.post(
            f"{API}/contracts",
            headers=h,
            json={
                "reservation_id": res["id"],
                "down_payment": "10000.00",
                "installments_count": 10,
                "interest_monthly_pct": "0",
                "first_due_date": (_today() + timedelta(days=30)).isoformat(),
            },
        ).json()
        if sign:
            assert (
                client.post(
                    f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1}
                ).status_code
                == 200
            )
        return c

    return {"h": h, "buyer": buyer, "other": other, "contract": contract}


def _invite(client, h, customer_id, **body):
    r = client.post(f"{API}/customers/{customer_id}/portal-invite", headers=h, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    url = urlparse(out["invite_path"])
    return out, parse_qs(url.query)["token"][0], url.path


def _activate(client, token, password=PW, slug="empresa-a"):
    return client.post(
        f"{API}/portal/auth/activate", json={"tenant_slug": slug, "token": token, "password": password}
    )


def _portal(client, email="comprador@example.com", password=PW, slug="empresa-a"):
    r = client.post(
        f"{API}/portal/auth/login", json={"tenant_slug": slug, "email": email, "password": password}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def ph(client, env):
    """Comprador com acesso ativo ao portal."""
    _, token, _ = _invite(client, env["h"], env["buyer"]["id"])
    assert _activate(client, token).status_code == 200
    return _portal(client)


# ─────────────────────────── convite e ativação ───────────────────────────


def test_invite_requires_email_and_returns_one_time_link(client, env, owner_engine):
    h = env["h"]
    r = client.post(f"{API}/customers/{env['other']['id']}/portal-invite", headers=h, json={})
    assert r.status_code == 409 and "e-mail" in r.json()["detail"]
    out, token, path = _invite(client, h, env["buyer"]["id"])
    assert path == "/portal/empresa-a/ativar"
    assert out["exists"] and out["email"] == "comprador@example.com" and not out["activated"]
    assert out["invite_expires_at"]
    status = client.get(f"{API}/customers/{env['buyer']['id']}/portal", headers=h).json()
    assert "invite_path" not in status and status["exists"] and not status["activated"]
    with owner_engine.begin() as c:  # só o hash é guardado
        hashes = c.execute(text("SELECT token_hash FROM portal_tokens")).scalars().all()
    assert token not in hashes and len(hashes[0]) == 64
    # e-mail já usado por outro comprador
    r = client.post(
        f"{API}/customers/{env['other']['id']}/portal-invite",
        headers=h,
        json={"email": "comprador@example.com"},
    )
    assert r.status_code == 409


def test_activation_is_single_use_and_checks_password(client, env):
    _, token, _ = _invite(client, env["h"], env["buyer"]["id"])
    assert _activate(client, token, password="curta").status_code == 409
    r = _activate(client, token)
    assert r.status_code == 200 and r.json()["access_token"]
    again = _activate(client, token)
    assert again.status_code == 409 and "inválido" in again.json()["detail"]
    assert _activate(client, token, slug="empresa-b").status_code == 409
    status = client.get(f"{API}/customers/{env['buyer']['id']}/portal", headers=env["h"]).json()
    assert status["activated"] and status["last_login_at"]


def test_new_invite_invalidates_previous_and_resets_password(client, env):
    h = env["h"]
    _, first, _ = _invite(client, h, env["buyer"]["id"])
    _, second, _ = _invite(client, h, env["buyer"]["id"])
    assert _activate(client, first).status_code == 409
    assert _activate(client, second).status_code == 200
    _, third, _ = _invite(client, h, env["buyer"]["id"])  # "esqueci a senha"
    assert _activate(client, third, password="nova-senha-123").status_code == 200
    r = client.post(
        f"{API}/portal/auth/login",
        json={"tenant_slug": "empresa-a", "email": "comprador@example.com", "password": PW},
    )
    assert r.status_code == 401
    _portal(client, password="nova-senha-123")


def test_expired_invite_is_rejected(client, env, owner_engine):
    _, token, _ = _invite(client, env["h"], env["buyer"]["id"])
    with owner_engine.begin() as c:
        c.execute(
            text("UPDATE portal_tokens SET expires_at = :t"), {"t": datetime.now(UTC) - timedelta(minutes=1)}
        )
    assert _activate(client, token).status_code == 409


# ─────────────────────────── login e sessão ───────────────────────────


def test_login_and_blocking(client, env, ph):
    h = env["h"]
    _portal(client, email="COMPRADOR@example.com")  # e-mail sem diferenciar maiúsculas
    bad = client.post(
        f"{API}/portal/auth/login",
        json={"tenant_slug": "empresa-a", "email": "comprador@example.com", "password": "errada"},
    )
    assert bad.status_code == 401
    me = client.get(f"{API}/portal/me", headers=ph).json()
    assert me == {
        "customer_name": "Comprador Portal",
        "email": "comprador@example.com",
        "company_name": "Empresa A",
        "company_slug": "empresa-a",
    }
    r = client.patch(
        f"{API}/customers/{env['buyer']['id']}/portal",
        headers=h,
        json={"is_active": False, "reason": "pedido"},
    )
    assert r.json()["is_active"] is False
    assert client.get(f"{API}/portal/me", headers=ph).status_code == 401  # sessão aberta cai na hora
    r = client.post(
        f"{API}/portal/auth/login",
        json={"tenant_slug": "empresa-a", "email": "comprador@example.com", "password": PW},
    )
    assert r.status_code == 401


def test_login_throttle(client, env, ph):
    body = {"tenant_slug": "empresa-a", "email": "comprador@example.com", "password": "errada"}
    codes = [client.post(f"{API}/portal/auth/login", json=body).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_portal_and_staff_tokens_do_not_mix(client, env, ph):
    assert client.get(f"{API}/me", headers=ph).status_code == 401
    assert client.get(f"{API}/customers", headers=ph).status_code == 401
    assert client.get(f"{API}/portal/me", headers=env["h"]).status_code == 401
    assert client.get(f"{API}/portal/contracts").status_code == 401


# ─────────────────────────── dados do comprador ───────────────────────────


def test_buyer_sees_only_own_signed_contracts(client, env, ph):
    mine = env["contract"](0, env["buyer"]["id"])
    env["contract"](1, env["buyer"]["id"], sign=False)  # rascunho: não aparece
    theirs = env["contract"](2, env["other"]["id"])
    listed = client.get(f"{API}/portal/contracts", headers=ph).json()
    assert [c["id"] for c in listed] == [mine["id"]]
    c = listed[0]
    assert (c["open_count"], c["paid_count"], c["open_balance"]) == (11, 0, "100000.00")
    assert c["next_due"]["number"] == 0 and c["next_due"]["remaining"] == "10000.00"
    assert client.get(f"{API}/portal/contracts/{theirs['id']}", headers=ph).status_code == 404
    detail = client.get(f"{API}/portal/contracts/{mine['id']}", headers=ph).json()
    assert len(detail["installments"]) == 11 and detail["terms"]["late_fee_pct"] == "2.00"
    # lista fechada de campos: nada interno (corretor, comissão, anotações, autor)
    assert set(detail) == {
        "id", "number", "status", "signed_at", "development_name", "block_code", "lot_number", "area_m2",
        "total_price", "paid_count", "open_count", "overdue_count", "open_balance", "next_due", "terms",
        "installments", "payments", "renegotiated",
    }  # fmt: skip
    assert [i["label"] for i in detail["installments"][:3]] == ["Entrada", "Parcela 1/10", "Parcela 2/10"]
    assert set(detail["installments"][0]) == {
        "id", "number", "label", "due_date", "amount", "remaining", "status", "overdue", "days_late",
        "paid_at",
        "pending_charge",
    }  # fmt: skip


def test_payments_and_statement(client, env, ph):
    h = env["h"]
    c = env["contract"](0, env["buyer"]["id"])
    inst = client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json()
    p1 = client.post(
        f"{API}/installments/{inst[0]['id']}/payments",
        headers=h,
        json={"amount": "10000.00", "notes": "interno"},
    ).json()
    p2 = client.post(
        f"{API}/installments/{inst[1]['id']}/payments", headers=h, json={"amount": "500.00"}
    ).json()
    client.post(f"{API}/payments/{p2['payments'][0]['id']}/reverse", headers=h, json={"reason": "devolvido"})
    detail = client.get(f"{API}/portal/contracts/{c['id']}", headers=ph).json()
    # mesma data: o lançamento mais recente vem primeiro
    assert [(p["amount"], p["reversed"]) for p in detail["payments"]] == [
        ("500.00", True),
        ("10000.00", False),
    ]
    assert all(
        set(p)
        == {
            "received_on",
            "installment_number",
            "installment_label",
            "amount",
            "principal",
            "late_charges",
            "method",
            "reversed",
        }
        for p in detail["payments"]
    )
    assert p1["status"] == "paga"
    year = _today().year
    st = client.get(f"{API}/portal/statement", headers=ph, params={"year": year}).json()
    assert (st["year"], st["total"], len(st["lines"])) == (year, "10000.00", 1)  # estornado não entra
    assert st["customer_name"] == "Comprador Portal" and st["company_name"] == "Empresa A"
    assert (
        client.get(f"{API}/portal/statement", headers=ph, params={"year": year - 1}).json()["total"] == "0.00"
    )


def test_second_copy_charge_is_idempotent_and_scoped(client, env, ph):
    h = env["h"]
    mine = env["contract"](0, env["buyer"]["id"])
    theirs = env["contract"](1, env["other"]["id"])
    inst = client.get(f"{API}/contracts/{mine['id']}/installments", headers=h).json()
    other_inst = client.get(f"{API}/contracts/{theirs['id']}/installments", headers=h).json()
    r = client.post(f"{API}/portal/installments/{inst[1]['id']}/charges", headers=ph, json={"method": "pix"})
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["method"] == "pix" and ch["amount"] == "9000.00" and ch["pix_code"]
    again = client.post(
        f"{API}/portal/installments/{inst[1]['id']}/charges", headers=ph, json={"method": "boleto"}
    )
    assert again.json()["id"] == ch["id"]  # mesma cobrança pendente
    detail = client.get(f"{API}/portal/contracts/{mine['id']}", headers=ph).json()
    assert detail["installments"][1]["pending_charge"]["id"] == ch["id"]
    assert (
        client.post(
            f"{API}/portal/installments/{other_inst[1]['id']}/charges", headers=ph, json={}
        ).status_code
        == 404
    )
    client.post(f"{API}/installments/{inst[0]['id']}/payments", headers=h, json={"amount": "10000.00"})
    paid = client.post(f"{API}/portal/installments/{inst[0]['id']}/charges", headers=ph, json={})
    assert paid.status_code == 409
    # a equipe vê a cobrança emitida pelo portal, sem autor (sistema)
    staff = client.get(f"{API}/charges/{ch['id']}", headers=h).json()
    assert staff["status"] == "pendente"
    q = client.get(f"{API}/portal/installments/{inst[2]['id']}/quote", headers=ph).json()
    assert q["total"] == "9000.00" and q["days_late"] == 0


# ─────────────────────────── isolamento, RLS e RBAC ───────────────────────────


def test_tenant_isolation(client, env, ph):
    bob = login(client, "bob", "empresa-b")
    r = client.post(f"{API}/customers/{env['buyer']['id']}/portal-invite", headers=bob, json={})
    assert r.status_code == 404
    assert client.get(f"{API}/customers/{env['buyer']['id']}/portal", headers=bob).status_code == 404
    r = client.post(
        f"{API}/portal/auth/login",
        json={"tenant_slug": "empresa-b", "email": "comprador@example.com", "password": PW},
    )
    assert r.status_code == 401


def test_rls_and_token_protection(app_engine, client, env, ph, world):
    with app_engine.begin() as c:
        for table in ("portal_accounts", "portal_tokens"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0  # noqa: S608
    with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as c:
        c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(world.tenant_a)})
        c.execute(text("UPDATE portal_tokens SET expires_at = now() + interval '1 year'"))


def test_rbac_for_invites(client, env):
    for user in ("carol", "fabi"):  # leitura e financeiro: não convidam
        hh = login(client, user)
        r = client.post(f"{API}/customers/{env['buyer']['id']}/portal-invite", headers=hh, json={})
        assert r.status_code == 403
    assert (
        client.get(f"{API}/customers/{env['buyer']['id']}/portal", headers=login(client, "fabi")).status_code
        == 200
    )
    dave = login(client, "dave")  # comercial convida
    assert (
        client.post(f"{API}/customers/{env['buyer']['id']}/portal-invite", headers=dave, json={}).status_code
        == 200
    )


def test_renegotiated_installments_are_numbered_from_the_new_plan(client, env, ph):
    h = env["h"]
    c = env["contract"](0, env["buyer"]["id"])
    r = client.post(
        f"{API}/contracts/{c['id']}/renegotiations",
        headers=h,
        json={
            "installments_count": 4,
            "first_due_date": (_today() + timedelta(days=20)).isoformat(),
            "reason": "pedido do cliente",
        },
    )
    assert r.status_code == 201, r.text
    detail = client.get(f"{API}/portal/contracts/{c['id']}", headers=ph).json()
    assert detail["renegotiated"] is True
    open_labels = [i["label"] for i in detail["installments"] if i["status"] == "aberta"]
    assert open_labels == [f"Parcela {k}/4 (renegociação)" for k in range(1, 5)]
    assert {i["status"] for i in detail["installments"] if "renegociação" not in i["label"]} == {
        "renegociada"
    }
