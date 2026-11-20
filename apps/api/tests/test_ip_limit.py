"""Limites por IP do visitante: IP assinado pelo web, login (painel + portal + links), "esqueci minha senha",
cadastro de empresa e formulário de contato."""

import pytest

from tests.conftest import PASSWORD

API = "/api/v1"
SECRET = "-".join(["teste"] * 8)  # valor fixo de teste (montado: não parece chave para o gitleaks)


# ─────────────────────────── unidade ───────────────────────────


def test_normalize_and_sign():
    from app.core.client_ip import normalize, sign

    assert normalize("200.141.27.53") == "200.141.27.53"
    assert normalize("2804:14c:1234:5678:aaaa:bbbb:cccc:dddd") == "2804:14c:1234:5678::/64"
    assert normalize("2804:14c:1234:5678::1") == normalize("2804:14c:1234:5678:ffff::2")  # mesmo bloco
    assert normalize("::ffff:10.0.0.7") == "10.0.0.7"
    assert normalize("não é ip") is None
    assert sign("1.2.3.4", SECRET) != sign("1.2.3.5", SECRET)


# ─────────────────────────── com banco ───────────────────────────


@pytest.fixture()
def ipsecret(monkeypatch):
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "client_ip_secret", SECRET)
    monkeypatch.setattr(s, "ip_login_failures", 3)
    monkeypatch.setattr(s, "ip_forgot_per_hour", 2)
    monkeypatch.setattr(s, "ip_signup_per_hour", 2)
    monkeypatch.setattr(s, "ip_leads_per_hour", 2)
    return s


BAD_LINK = {"token": "x" * 40, "new_password": PASSWORD + "-nova"}


def _ip(ip: str, *, sig: str | None = None) -> dict[str, str]:
    from app.core.client_ip import sign

    return {"X-Client-IP": ip, "X-Client-IP-Sig": sig or sign(ip, SECRET)}


def _login(client, headers, email="alice@example.com", password=PASSWORD):
    return client.post(
        f"{API}/auth/login",
        json={"tenant_slug": "empresa-a", "email": email, "password": password},
        headers=headers,
    )


@pytest.mark.db
def test_login_failures_from_one_ip_block_that_ip_only(client, world, ipsecret):
    a = _ip("200.0.0.1")
    for i in range(3):  # erros em e-mails diferentes (o limite por e-mail não pega)
        assert _login(client, a, email=f"x{i}@example.com", password="errada").status_code == 401
    r = _login(client, a)  # até a senha certa é barrada deste IP
    assert r.status_code == 429 and "endereço" in r.json()["detail"]
    assert _login(client, _ip("200.0.0.2")).status_code == 200  # outro IP segue normal
    assert _login(client, {}).status_code == 200  # sem IP assinado: sem limite por IP


@pytest.mark.db
def test_forged_or_missing_signature_is_ignored(client, world, ipsecret, monkeypatch):
    bad = _ip("200.0.0.9", sig="0" * 64)
    for i in range(5):
        _login(client, bad, email=f"y{i}@example.com", password="errada")
    assert _login(client, bad).status_code == 200  # IP não confiável: não conta nem bloqueia
    # sem segredo configurado, o cabeçalho é ignorado mesmo assinado
    monkeypatch.setattr(ipsecret, "client_ip_secret", None)
    good = _ip("200.0.0.10")
    for i in range(5):
        _login(client, good, email=f"z{i}@example.com", password="errada")
    assert _login(client, good).status_code == 200


@pytest.mark.db
def test_portal_and_reset_link_errors_share_the_login_bucket(client, world, ipsecret):
    a = _ip("200.0.0.3")
    r = client.post(
        f"{API}/portal/auth/login",
        json={"tenant_slug": "empresa-a", "email": "comprador@example.com", "password": "errada"},
        headers=a,
    )
    assert r.status_code == 401
    r = client.post(f"{API}/auth/password/reset", json=BAD_LINK, headers=a)
    assert r.status_code == 404
    _login(client, a, password="errada")
    assert _login(client, a).status_code == 429  # 3 erros somados: portal + link + painel
    r = client.post(f"{API}/auth/password/reset", json=BAD_LINK, headers=a)
    assert r.status_code == 429


@pytest.mark.db
def test_forgot_password_limit_per_ip_is_silent(client, world, ipsecret, monkeypatch):
    from app.services import email

    sent: list = []
    monkeypatch.setattr(email, "TRANSPORT_OVERRIDE", sent.append)
    monkeypatch.setattr(ipsecret, "public_web_url", "https://imobil.exemplo.com.br")
    a = _ip("200.0.0.4")
    for user in ("alice", "carol", "erin"):
        r = client.post(f"{API}/auth/password/forgot", json={"email": f"{user}@example.com"}, headers=a)
        assert r.status_code == 202  # mesma resposta sempre
    assert [m["To"] for m in sent] == ["alice@example.com", "carol@example.com"]  # o 3º não sai
    client.post(f"{API}/auth/password/forgot", json={"email": "erin@example.com"}, headers=_ip("200.0.0.5"))
    assert sent[-1]["To"] == "erin@example.com"


@pytest.mark.db
def test_signup_limit_per_ip(client, world, ipsecret):
    from app.api.v1.billing import reset_signup_limit

    reset_signup_limit()
    a = _ip("200.0.0.6")

    def signup(n, headers):
        return client.post(
            f"{API}/signup",
            json={
                "company_name": f"Empresa {n}",
                "slug": f"empresa-ip-{n}",
                "full_name": "Dona",
                "email": f"dona{n}@exemplo-ip.com",
                "password": "senha-da-empresa",
                "plan_code": "essencial",
                "accept_terms": True,
            },
            headers=headers,
        )

    assert [signup(n, a).status_code for n in (1, 2)] == [201, 201]
    r = signup(3, a)
    assert r.status_code == 429 and "endereço" in r.json()["detail"]
    assert signup(4, _ip("200.0.0.7")).status_code == 201


@pytest.mark.db
def test_site_lead_limit_per_ip(client, world, ipsecret):
    from tests.conftest import login

    h = login(client, "alice")
    client.put(f"{API}/site/settings", headers=h, json={"published": True, "lead_form_enabled": True})
    a = _ip("200.0.0.8")

    def lead(n, headers):
        return client.post(
            f"{API}/public/sites/empresa-a/leads",
            json={"name": f"Pessoa {n}", "email": f"pessoa{n}@example.com", "consent": True},
            headers=headers,
        )

    assert [lead(n, a).status_code for n in (1, 2)] == [202, 202]
    assert lead(3, a).status_code == 429
    assert lead(4, _ip("200.0.0.11")).status_code == 202
