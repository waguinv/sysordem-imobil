"""Senha: troca (encerra as outras sessões), "esqueci minha senha", link de uso único, convite por e-mail,
limite de pedidos, MFA preservado e permissões do banco."""

import re

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import PASSWORD, login

pytestmark = pytest.mark.db

API = "/api/v1"
NEW = "nova-senha-bem-longa-42"


@pytest.fixture()
def outbox(monkeypatch):
    from app.core.config import get_settings
    from app.services import email

    sent: list = []
    monkeypatch.setattr(email, "TRANSPORT_OVERRIDE", sent.append)
    monkeypatch.setattr(get_settings(), "public_web_url", "https://imobil.exemplo.com.br")
    return sent


def _link_token(msg) -> str:
    body = msg.get_content()
    m = re.search(r"https://imobil\.exemplo\.com\.br/redefinir-senha\?token=([\w-]+)", body)
    assert m, body
    return m.group(1)


def _login_raw(client, user="alice", password=PASSWORD, tenant="empresa-a"):
    return client.post(
        f"{API}/auth/login",
        json={"tenant_slug": tenant, "email": f"{user}@example.com", "password": password},
    )


# ─────────────────────────── trocar a senha ───────────────────────────


def test_change_password_ends_other_sessions(client, world):
    old_session = login(client, "alice")
    other_device = login(client, "alice")
    url = f"{API}/auth/password/change"
    bad = client.post(url, headers=old_session, json={"current_password": "errada", "new_password": NEW})
    assert bad.status_code == 422 and "atual incorreta" in bad.json()["detail"]
    weak = client.post(url, headers=old_session, json={"current_password": PASSWORD, "new_password": "curta"})
    assert weak.status_code == 422 and "12 caracteres" in weak.json()["detail"]
    same = client.post(
        url, headers=old_session, json={"current_password": PASSWORD, "new_password": PASSWORD}
    )
    assert same.status_code == 422
    r = client.post(url, headers=old_session, json={"current_password": PASSWORD, "new_password": NEW})
    assert r.status_code == 200, r.text
    fresh = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get(f"{API}/me", headers=fresh).status_code == 200  # a sessão nova vale
    assert client.get(f"{API}/me", headers=other_device).status_code == 401  # outro aparelho caiu
    assert _login_raw(client).status_code == 401
    assert _login_raw(client, password=NEW).status_code == 200
    assert "auth.password_change" in {
        a["action"] for a in client.get(f"{API}/audit-events", headers=fresh).json()
    }


# ─────────────────────────── esqueci minha senha ───────────────────────────


def test_forgot_without_email_configured_says_so(client, world, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "smtp_host", None)
    r = client.post(f"{API}/auth/password/forgot", json={"email": "alice@example.com"})
    assert r.status_code == 202 and r.json() == {"status": "recebido", "email_enabled": False}


def test_forgot_and_reset_with_single_use_link(client, world, outbox, owner_engine):
    session = login(client, "alice")
    url = f"{API}/auth/password/forgot"
    # e-mail inexistente: mesma resposta, nada enviado
    r = client.post(url, json={"email": "ninguem@example.com", "tenant_slug": "empresa-a"})
    assert r.status_code == 202 and r.json()["email_enabled"] is True and outbox == []
    # existente: link de redefinição
    client.post(url, json={"email": "ALICE@example.com", "tenant_slug": "empresa-a"})
    [msg] = outbox
    assert msg["To"] == "alice@example.com" and "Redefinir" in msg["Subject"]
    first = _link_token(msg)
    # pedir de novo invalida o link anterior
    client.post(url, json={"email": "alice@example.com", "tenant_slug": "empresa-a"})
    token = _link_token(outbox[1])
    assert client.get(f"{API}/auth/password/token", params={"token": first}).status_code == 404
    with owner_engine.begin() as c:  # no banco só o hash
        assert (
            c.execute(
                text("SELECT count(*) FROM password_tokens WHERE token_hash = :t"), {"t": token}
            ).scalar()
            == 0
        )

    info = client.get(f"{API}/auth/password/token", params={"token": token}).json()
    assert info["purpose"] == "redefinir" and info["email"] == "alice@example.com"
    weak = client.post(f"{API}/auth/password/reset", json={"token": token, "new_password": "alice"})
    assert weak.status_code == 422
    r = client.post(f"{API}/auth/password/reset", json={"token": token, "new_password": NEW})
    assert r.status_code == 200 and r.json() == {"purpose": "redefinir", "tenant_slug": "empresa-a"}
    # uso único; sessões antigas caem; senha nova vale
    assert (
        client.post(
            f"{API}/auth/password/reset", json={"token": token, "new_password": NEW + "x"}
        ).status_code
        == 404
    )
    assert client.get(f"{API}/me", headers=session).status_code == 401
    assert _login_raw(client, password=NEW).status_code == 200
    fresh = {"Authorization": "Bearer " + _login_raw(client, password=NEW).json()["access_token"]}
    actions = {a["action"] for a in client.get(f"{API}/audit-events", headers=fresh).json()}
    assert {"auth.password_reset_request", "auth.password_reset"} <= actions


def test_expired_link_and_request_limit(client, world, outbox, owner_engine):
    url = f"{API}/auth/password/forgot"
    client.post(url, json={"email": "alice@example.com"})
    token = _link_token(outbox[0])
    with owner_engine.begin() as c:
        c.execute(text("UPDATE password_tokens SET expires_at = now() - interval '1 minute'"))
    assert client.get(f"{API}/auth/password/token", params={"token": token}).status_code == 404
    assert (
        client.post(f"{API}/auth/password/reset", json={"token": token, "new_password": NEW}).status_code
        == 404
    )
    for _ in range(4):  # limite: 3 por e-mail por hora (o 1º já contou)
        assert client.post(url, json={"email": "alice@example.com"}).status_code == 202
    assert len(outbox) == 3


# ─────────────────────────── convite ───────────────────────────


def _member(email_addr, **extra):
    return {
        "email": email_addr,
        "full_name": "Pessoa Convidada",
        "role": "comercial",
        "reason": "equipe",
        **extra,
    }


def test_invite_new_member_by_email(client, world, outbox):
    h = login(client, "alice")
    assert client.get(f"{API}/me", headers=h).json()["email_enabled"] is True
    r = client.post(f"{API}/members", headers=h, json=_member("nova@example.com"))
    assert r.status_code == 201, r.text
    assert r.json()["invite_sent"] is True
    [msg] = outbox
    assert msg["To"] == "nova@example.com" and "Convite para Empresa A" in msg["Subject"]
    assert "Alice convidou você" in msg.get_content()
    token = _link_token(msg)
    assert client.get(f"{API}/auth/password/token", params={"token": token}).json()["purpose"] == "convite"
    r = client.post(f"{API}/auth/password/reset", json={"token": token, "new_password": NEW})
    assert r.json() == {"purpose": "convite", "tenant_slug": "empresa-a"}
    ok = client.post(
        f"{API}/auth/login", json={"tenant_slug": "empresa-a", "email": "nova@example.com", "password": NEW}
    )
    assert ok.status_code == 200 and ok.json()["access_token"]

    # quem já tem conta (em outra empresa) só recebe o aviso do novo acesso — sem link de senha
    hb = login(client, "bob", "empresa-b")
    r = client.post(f"{API}/members", headers=hb, json=_member("carol@example.com", role="leitura"))
    assert r.status_code == 201 and r.json()["invite_sent"] is True
    assert (
        "Novo acesso: Empresa B" in outbox[-1]["Subject"]
        and "redefinir-senha" not in outbox[-1].get_content()
    )


def test_invite_needs_email_or_initial_password(client, world, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "smtp_host", None)
    h = login(client, "alice")
    r = client.post(f"{API}/members", headers=h, json=_member("sem@example.com"))
    assert r.status_code == 422 and "senha inicial" in r.json()["detail"]
    weak = client.post(
        f"{API}/members", headers=h, json=_member("sem@example.com", initial_password="123456789012")
    )
    assert weak.status_code == 422
    ok = client.post(f"{API}/members", headers=h, json=_member("sem@example.com", initial_password=NEW))
    assert ok.status_code == 201 and ok.json()["invite_sent"] is False


# ─────────────────────────── MFA e banco ───────────────────────────


def test_reset_keeps_mfa_and_cancels_pending_challenge(client, world, outbox, monkeypatch):
    import base64
    import os

    from app.core import credentials, totp
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "payments_credentials_key", base64.b64encode(os.urandom(32)).decode())
    credentials._aead.cache_clear()
    try:
        h = login(client, "alice")
        secret = client.post(f"{API}/auth/mfa/setup", headers=h, json={"password": PASSWORD}).json()["secret"]
        client.post(
            f"{API}/auth/mfa/enable", headers=h, json={"code": totp.code_at(secret, totp.current_step())}
        )
        challenge = _login_raw(client).json()["mfa_token"]
        client.post(f"{API}/auth/password/forgot", json={"email": "alice@example.com"})
        client.post(f"{API}/auth/password/reset", json={"token": _link_token(outbox[0]), "new_password": NEW})
        # o desafio emitido antes da redefinição não vale mais
        code = totp.code_at(secret, totp.current_step() + 1)
        r = client.post(f"{API}/auth/mfa/login", json={"mfa_token": challenge, "code": code})
        assert r.status_code == 401 and "expirou" in r.json()["detail"]
        # e a senha nova continua pedindo o código (o MFA não foi desligado)
        assert _login_raw(client, password=NEW).json()["mfa_required"] is True
    finally:
        credentials._aead.cache_clear()


def test_password_tokens_table_is_protected(client, world, outbox, app_engine):
    client.post(f"{API}/auth/password/forgot", json={"email": "alice@example.com"})
    for sql in (
        "DELETE FROM password_tokens",
        "UPDATE password_tokens SET expires_at = now() + interval '9 days'",
    ):
        with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as c:
            c.execute(text(sql))


def test_password_change_unit_rules():
    from app.services.passwords import PasswordError, validate

    for bad in ("curta", "123456789012", "aaaaaaaaaaaaaaa", "alice@example.com"):
        with pytest.raises(PasswordError):
            validate(bad, email="alice@example.com")
    validate("uma-frase-longa-e-boa", email="alice@example.com")
