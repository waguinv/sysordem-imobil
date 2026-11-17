"""Verificação em duas etapas: TOTP (RFC 6238), ativação, login em 2 etapas, reuso, recuperação,
limite de tentativas, várias empresas e permissões do banco."""

import base64
import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from tests.conftest import PASSWORD, login

API = "/api/v1"

# ─────────────────────────── TOTP (sem banco) ───────────────────────────

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    ("unix", "expected8"),
    [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037")],
)
def test_totp_rfc6238_vectors(unix, expected8):
    from app.core import totp

    step = unix // 30
    assert totp.code_at(RFC_SECRET, step, digits=8) == expected8
    assert totp.code_at(RFC_SECRET, step) == expected8[-6:]


def test_totp_window_and_replay():
    from app.core import totp

    now = 1234567890.0
    base = totp.current_step(now)
    for off in (-1, 0, 1):
        assert (
            totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, base + off), last_step=None, now=now)
            == base + off
        )
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, base + 2), last_step=None, now=now) is None
    assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, base), last_step=base, now=now) is None  # reuso
    assert totp.verify(RFC_SECRET, "12345", last_step=None, now=now) is None
    assert totp.verify(RFC_SECRET, "abcdef", last_step=None, now=now) is None
    uri = totp.otpauth_uri("ABC", account="a@b.com", issuer="SysOrdem Imobil")
    assert uri.startswith("otpauth://totp/SysOrdem%20Imobil%3Aa%40b.com?secret=ABC&issuer=SysOrdem+Imobil")


# ─────────────────────────── fluxo com banco ───────────────────────────


@pytest.fixture()
def vault(monkeypatch):
    from app.core import credentials
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "payments_credentials_key", base64.b64encode(os.urandom(32)).decode())
    credentials._aead.cache_clear()
    yield
    credentials._aead.cache_clear()


def _code(secret: str, offset: int = 0) -> str:
    from app.core import totp

    return totp.code_at(secret, totp.current_step() + offset)


def _enable(client, h):
    r = client.post(f"{API}/auth/mfa/setup", headers=h, json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = client.post(f"{API}/auth/mfa/enable", headers=h, json={"code": _code(secret)})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def _password_step(client, user="alice", tenant="empresa-a"):
    r = client.post(
        f"{API}/auth/login",
        json={"tenant_slug": tenant, "email": f"{user}@example.com", "password": PASSWORD},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.db
def test_enable_and_login_in_two_steps(client, world, vault, owner_engine):
    h = login(client, "alice")
    st = client.get(f"{API}/auth/mfa", headers=h).json()
    assert st == {"enabled": False, "available": True, "recovery_codes_left": 0, "enabled_at": None}
    me = client.get(f"{API}/me", headers=h).json()
    assert me["mfa_enabled"] is False and me["mfa_recommended"] is True  # proprietária

    bad = client.post(f"{API}/auth/mfa/setup", headers=h, json={"password": "senha-errada"})
    assert bad.status_code == 422 and "Senha incorreta" in bad.json()["detail"]
    r = client.post(f"{API}/auth/mfa/setup", headers=h, json={"password": PASSWORD})
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/") and len(secret) == 32
    assert client.post(f"{API}/auth/mfa/enable", headers=h, json={"code": "000000"}).status_code == 422
    r = client.post(f"{API}/auth/mfa/enable", headers=h, json={"code": _code(secret)})
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10 and all(len(c) == 11 and c[5] == "-" for c in codes)
    st = client.get(f"{API}/auth/mfa", headers=h).json()
    assert st["enabled"] and st["recovery_codes_left"] == 10
    with owner_engine.begin() as c:  # segredo nunca em texto puro
        sealed = c.execute(text("SELECT secret_sealed FROM user_mfa")).scalar()
    assert secret not in sealed

    # 1ª etapa: senha certa NÃO dá acesso — só o desafio
    step1 = _password_step(client)
    assert step1["mfa_required"] and step1["access_token"] is None and step1["expires_in"] == 300
    assert (
        client.get(f"{API}/me", headers={"Authorization": f"Bearer {step1['mfa_token']}"}).status_code == 401
    )
    wrong = client.post(f"{API}/auth/mfa/login", json={"mfa_token": step1["mfa_token"], "code": "123456"})
    assert wrong.status_code == 401 and wrong.json()["detail"] == "Código inválido"
    # 2ª etapa: o código do aplicativo (o próximo intervalo; o atual já foi usado na ativação)
    ok = client.post(
        f"{API}/auth/mfa/login", json={"mfa_token": step1["mfa_token"], "code": _code(secret, 1)}
    )
    assert ok.status_code == 200, ok.text
    token = ok.json()["access_token"]
    assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {token}"}).json()["mfa_enabled"] is True
    # o mesmo código não vale de novo (reuso)
    again = client.post(
        f"{API}/auth/mfa/login", json={"mfa_token": step1["mfa_token"], "code": _code(secret, 1)}
    )
    assert again.status_code == 401

    # código de recuperação: vale uma vez (aceita sem hífen e com maiúsculas)
    step1 = _password_step(client)
    rc = codes[0]
    r = client.post(
        f"{API}/auth/mfa/login", json={"mfa_token": step1["mfa_token"], "code": rc.replace("-", "").upper()}
    )
    assert r.status_code == 200
    r = client.post(f"{API}/auth/mfa/login", json={"mfa_token": step1["mfa_token"], "code": rc})
    assert r.status_code == 401
    assert client.get(f"{API}/auth/mfa", headers=h).json()["recovery_codes_left"] == 9
    logins = [a for a in client.get(f"{API}/audit-events", headers=h).json() if a["action"] == "auth.login"]
    assert {a["data"].get("mfa") for a in logins} >= {"totp", "recuperacao"}
    assert "auth.mfa_enable" in {a["action"] for a in client.get(f"{API}/audit-events", headers=h).json()}


@pytest.mark.db
def test_bad_or_expired_challenge_and_throttle(client, world, vault):
    h = login(client, "alice")
    _enable(client, h)
    for bad in ("x" * 20, login(client, "carol")["Authorization"].split()[1]):  # lixo / token de acesso
        r = client.post(f"{API}/auth/mfa/login", json={"mfa_token": bad, "code": "123456"})
        assert r.status_code == 401 and "expirou" in r.json()["detail"]
    step1 = _password_step(client)
    for _ in range(5):
        assert (
            client.post(
                f"{API}/auth/mfa/login", json={"mfa_token": step1["mfa_token"], "code": "000000"}
            ).status_code
            == 401
        )
    r = client.post(f"{API}/auth/mfa/login", json={"mfa_token": step1["mfa_token"], "code": "000000"})
    assert r.status_code == 429  # limite por usuário (a senha certa não "zera" o limite do código)


@pytest.mark.db
def test_disable_and_regenerate_require_password_and_code(client, world, vault):
    h = login(client, "alice")
    secret, codes = _enable(client, h)
    assert (
        client.post(
            f"{API}/auth/mfa/disable", headers=h, json={"password": "errada", "code": codes[0]}
        ).status_code
        == 422
    )
    r = client.post(
        f"{API}/auth/mfa/recovery-codes", headers=h, json={"password": PASSWORD, "code": codes[0]}
    )
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(codes)
    # os códigos antigos deixaram de valer
    r = client.post(f"{API}/auth/mfa/disable", headers=h, json={"password": PASSWORD, "code": codes[1]})
    assert r.status_code == 422
    r = client.post(f"{API}/auth/mfa/disable", headers=h, json={"password": PASSWORD, "code": new[0]})
    assert r.status_code == 200 and r.json()["enabled"] is False
    step = _password_step(client)
    assert step["mfa_required"] is False and step["access_token"]  # volta ao login só com senha
    assert "auth.mfa_disable" in {a["action"] for a in client.get(f"{API}/audit-events", headers=h).json()}


@pytest.mark.db
def test_mfa_is_per_user_across_companies_and_listed_in_team(client, world, vault):
    hd = login(client, "dave")  # comercial na A, leitura na B
    assert client.get(f"{API}/me", headers=hd).json()["mfa_recommended"] is False
    _enable(client, hd)
    assert _password_step(client, "dave", "empresa-b")["mfa_required"] is True  # vale na outra empresa
    members = client.get(f"{API}/members", headers=login(client, "alice")).json()
    by_email = {m["email"]: m["mfa_enabled"] for m in members}
    assert by_email["dave@example.com"] is True and by_email["alice@example.com"] is False


@pytest.mark.db
def test_without_vault_key_mfa_is_unavailable(client, world, monkeypatch):
    from app.core import credentials
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "payments_credentials_key", None)
    credentials._aead.cache_clear()
    try:
        h = login(client, "alice")
        assert client.get(f"{API}/auth/mfa", headers=h).json()["available"] is False
        r = client.post(f"{API}/auth/mfa/setup", headers=h, json={"password": PASSWORD})
        assert r.status_code == 503
    finally:
        credentials._aead.cache_clear()


@pytest.mark.db
def test_recovery_codes_table_is_protected(client, world, vault, app_engine):
    h = login(client, "alice")
    _enable(client, h)
    for sql in ("DELETE FROM user_mfa_recovery_codes", "UPDATE user_mfa_recovery_codes SET code_hash = 'x'"):
        with pytest.raises(ProgrammingError, match="permission denied"), app_engine.begin() as c:
            c.execute(text(sql))
