"""Isolamento entre tenants e RBAC (testes negativos) pela API, conectada como papel sujeito ao RLS."""

import uuid

import pytest

from tests.conftest import PASSWORD, login

pytestmark = pytest.mark.db

NEW_PW = "senha-inicial-forte"


def test_login_returns_token_scoped_to_tenant(client):
    me = client.get("/api/v1/me", headers=login(client, "alice")).json()
    assert me["tenant"]["slug"] == "empresa-a"
    assert me["role"] == "owner"
    assert "members:manage" in me["permissions"]


def test_login_rejects_user_without_membership_in_tenant(client):
    body = {"tenant_slug": "empresa-b", "email": "alice@example.com", "password": PASSWORD}
    assert client.post("/api/v1/auth/login", json=body).status_code == 401


def test_login_wrong_password_and_unknown_user_same_error(client):
    for email, pw in [("alice@example.com", "errada"), ("ninguem@example.com", PASSWORD)]:
        r = client.post(
            "/api/v1/auth/login", json={"tenant_slug": "empresa-a", "email": email, "password": pw}
        )
        assert r.status_code == 401
        assert r.json()["detail"] == "Credenciais inválidas"


def test_login_throttled_after_failures(client):
    body = {"tenant_slug": "empresa-a", "email": "alice@example.com", "password": "errada"}
    codes = [client.post("/api/v1/auth/login", json=body).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429


def test_tenant_a_lists_only_own_members(client, world):
    a = client.get("/api/v1/members", headers=login(client, "alice")).json()
    b = client.get("/api/v1/members", headers=login(client, "bob", "empresa-b")).json()
    a_ids = {m["id"] for m in a}
    b_ids = {m["id"] for m in b}
    assert a_ids == {str(v) for k, v in world.members.items() if k[1] == "a"}
    assert b_ids == {str(v) for k, v in world.members.items() if k[1] == "b"}
    assert a_ids.isdisjoint(b_ids)


def test_forged_tenant_header_and_query_ignored(client, world):
    h = login(client, "alice") | {"X-Tenant-Id": str(world.tenant_b)}
    r = client.get(f"/api/v1/members?tenant_id={world.tenant_b}", headers=h)
    assert r.status_code == 200
    assert str(world.members[("bob", "b")]) not in {m["id"] for m in r.json()}
    assert client.get("/api/v1/tenant", headers=h).json()["slug"] == "empresa-a"


def test_cannot_update_member_of_other_tenant(client, world):
    r = client.patch(
        f"/api/v1/members/{world.members[('bob', 'b')]}",
        headers=login(client, "alice"),
        json={"is_active": False, "reason": "tentativa cruzada"},
    )
    assert r.status_code == 404


def test_created_member_lands_in_actor_tenant_only(client, world):
    r = client.post(
        "/api/v1/members",
        headers=login(client, "alice"),
        json={
            "email": "novo@example.com",
            "full_name": "Novo Usuário",
            "role": "comercial",
            "initial_password": NEW_PW,
            "reason": "contratação",
            "tenant_id": str(world.tenant_b),  # campo forjado: ignorado
        },
    )
    assert r.status_code == 201, r.text
    b = client.get("/api/v1/members", headers=login(client, "bob", "empresa-b")).json()
    assert "novo@example.com" not in {m["email"] for m in b}
    body = {"tenant_slug": "empresa-b", "email": "novo@example.com", "password": NEW_PW}
    assert client.post("/api/v1/auth/login", json=body).status_code == 401


def test_switch_tenant_requires_membership(client):
    r = client.post(
        "/api/v1/auth/switch-tenant", headers=login(client, "dave"), json={"tenant_slug": "empresa-b"}
    )
    assert r.status_code == 200
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert me["tenant"]["slug"] == "empresa-b"
    assert me["role"] == "leitura"

    r = client.post(
        "/api/v1/auth/switch-tenant", headers=login(client, "alice"), json={"tenant_slug": "empresa-b"}
    )
    assert r.status_code == 403


def test_revoked_membership_invalidates_existing_token(client, world):
    carol = login(client, "carol")
    assert client.get("/api/v1/me", headers=carol).status_code == 200
    r = client.patch(
        f"/api/v1/members/{world.members[('carol', 'a')]}",
        headers=login(client, "alice"),
        json={"is_active": False, "reason": "desligamento"},
    )
    assert r.status_code == 200
    assert client.get("/api/v1/me", headers=carol).status_code == 401


def test_token_mixing_tenant_and_foreign_membership_is_rejected(client, world):
    from app.core.security import create_access_token

    forged = create_access_token(
        user_id=world.users["bob"], tenant_id=world.tenant_a, membership_id=world.members[("bob", "b")]
    )
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


# ---- RBAC negativo ----


def test_leitura_cannot_view_or_manage_members(client):
    h = login(client, "carol")
    assert client.get("/api/v1/members", headers=h).status_code == 403
    body = {"email": "x@example.com", "full_name": "X", "role": "leitura", "initial_password": NEW_PW}
    assert client.post("/api/v1/members", headers=h, json=body).status_code == 403
    assert client.get("/api/v1/audit-events", headers=h).status_code == 403


def test_admin_cannot_create_or_change_owner(client, world):
    h = login(client, "erin")
    body = {"email": "dono2@example.com", "full_name": "Dono 2", "role": "owner", "initial_password": NEW_PW}
    assert client.post("/api/v1/members", headers=h, json=body).status_code == 403
    r = client.patch(
        f"/api/v1/members/{world.members[('alice', 'a')]}",
        headers=h,
        json={"is_active": False, "reason": "golpe"},
    )
    assert r.status_code == 403


def test_member_changes_are_audited_with_reason(client, world):
    h = login(client, "alice")
    client.patch(
        f"/api/v1/members/{world.members[('dave', 'a')]}",
        headers=h,
        json={"role": "financeiro", "reason": "mudança de função"},
    )
    events = client.get("/api/v1/audit-events", headers=h).json()
    upd = next(e for e in events if e["action"] == "member.update")
    assert upd["actor_user_id"] == str(world.users["alice"])
    assert upd["reason"] == "mudança de função"
    assert upd["data"]["before"]["role"] == "comercial"
    assert upd["data"]["after"]["role"] == "financeiro"
    b_events = client.get("/api/v1/audit-events", headers=login(client, "bob", "empresa-b")).json()
    assert all(e["entity_id"] != str(world.members[("dave", "a")]) for e in b_events)


def test_unknown_member_id_is_404(client):
    r = client.patch(
        f"/api/v1/members/{uuid.uuid4()}",
        headers=login(client, "alice"),
        json={"is_active": False, "reason": "teste"},
    )
    assert r.status_code == 404
