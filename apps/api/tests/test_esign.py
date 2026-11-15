"""Assinatura eletrônica: envio, travas no contrato, conclusão confirmada no provedor, webhooks autenticados
e deduplicados, isolamento, permissões e o adaptador Documenso (contra um servidor falso)."""

import json
from datetime import timedelta

import httpx
import pytest

from tests.conftest import login

pytestmark = pytest.mark.db

API = "/api/v1"


def _today():
    from app.core.clock import business_today

    return business_today()


@pytest.fixture()
def env(client):
    h = login(client, "alice")
    dev = client.post(f"{API}/developments", headers=h, json={"name": "Residencial Assinatura"}).json()
    block = client.post(f"{API}/developments/{dev['id']}/blocks", headers=h, json={"code": "S"}).json()
    lots = client.post(
        f"{API}/developments/{dev['id']}/lots/batch",
        headers=h,
        json={"block_id": block["id"], "first": 1, "last": 2, "area_m2": "300", "price": "120000.00"},
    ).json()
    customer = client.post(
        f"{API}/customers", headers=h, json={"name": "Comprador Assinante", "email": "comprador@example.com"}
    ).json()

    def contract(lot_idx: int = 0, customer_id: str | None = None):
        res = client.post(
            f"{API}/reservations",
            headers=h,
            json={"lot_id": lots[lot_idx]["id"], "customer_id": customer_id or customer["id"]},
        ).json()
        c = client.post(
            f"{API}/contracts",
            headers=h,
            json={
                "reservation_id": res["id"],
                "down_payment": "20000.00",
                "installments_count": 10,
                "first_due_date": (_today() + timedelta(days=30)).isoformat(),
            },
        )
        assert c.status_code == 201, c.text
        return c.json()

    return {"h": h, "contract": contract, "lots": lots, "customer": customer}


def _send(client, h, contract_id, version=1, **extra):
    return client.post(
        f"{API}/contracts/{contract_id}/signature-requests",
        headers=h,
        json={"expected_version": version, **extra},
    )


def _simulate(client, h, req_id, action="sign_all", reason=None):
    return client.post(
        f"{API}/signature-requests/{req_id}/sandbox/simulate",
        headers=h,
        json={"action": action, "reason": reason},
    )


# ─────────────────────────── envio ───────────────────────────


def test_send_creates_request_with_document_hash(client, env):
    h = env["h"]
    c = env["contract"]()
    r = _send(client, h, c["id"])
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "enviado" and req["provider"] == "sandbox" and req["contract_version"] == 1
    assert len(req["document_sha256"]) == 64
    assert [(s["role"], s["email"], s["status"]) for s in req["signers"]] == [
        ("comprador", "comprador@example.com", "pendente"),
        ("empresa", "alice@example.com", "pendente"),
    ]
    listed = client.get(f"{API}/contracts/{c['id']}/signature-requests", headers=h).json()
    assert [x["id"] for x in listed] == [req["id"]]
    pdf = client.get(f"{API}/contracts/{c['id']}/document.pdf", headers=h)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["x-content-sha256"] == req["document_sha256"]  # mesmo documento enviado
    events = client.get(f"{API}/audit-events", headers=h).json()
    assert any(e["action"] == "contract.esign_send" for e in events)


def test_contract_is_frozen_while_signature_is_pending(client, env):
    h = env["h"]
    c = env["contract"]()
    assert _send(client, h, c["id"]).status_code == 201
    due = (_today() + timedelta(days=30)).isoformat()
    v = client.post(
        f"{API}/contracts/{c['id']}/versions",
        headers=h,
        json={
            "down_payment": "30000.00",
            "installments_count": 9,
            "first_due_date": due,
            "expected_version": 1,
            "reason": "mudança",
        },
    )
    assert v.status_code == 409 and "assinatura eletrônica" in v.json()["detail"]
    assert (
        client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1}).status_code
        == 409
    )
    assert (
        client.post(f"{API}/contracts/{c['id']}/cancel", headers=h, json={"reason": "desistiu"}).status_code
        == 409
    )
    again = _send(client, h, c["id"])
    assert again.status_code == 409 and "em andamento" in again.json()["detail"]


def test_send_requires_draft_current_version_and_customer_email(client, env):
    h = env["h"]
    c = env["contract"]()
    assert _send(client, h, c["id"], version=2).status_code == 409
    no_email = client.post(f"{API}/customers", headers=h, json={"name": "Sem E-mail"}).json()
    c2 = env["contract"](lot_idx=1, customer_id=no_email["id"])
    r = _send(client, h, c2["id"])
    assert r.status_code == 409 and "e-mail do comprador" in r.json()["detail"]
    client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1})
    assert _send(client, h, c["id"]).status_code == 409  # já assinado


# ─────────────────────────── conclusão, recusa e cancelamento ───────────────────────────


def test_all_signatures_sign_the_contract(client, env):
    h = env["h"]
    c = env["contract"]()
    req = _send(client, h, c["id"]).json()
    r = _simulate(client, h, req["id"])
    assert r.status_code == 200 and r.json()["result"] == "assinado", r.text
    done = r.json()["request"]
    assert done["status"] == "assinado" and done["completed_at"]
    assert all(s["status"] == "assinou" for s in done["signers"])
    contract = client.get(f"{API}/contracts/{c['id']}", headers=h).json()
    assert contract["status"] == "assinado"
    assert client.get(f"{API}/lots/{env['lots'][0]['id']}", headers=h).json()["status"] == "vendido"
    events = client.get(f"{API}/audit-events", headers=h).json()
    sign = next(e for e in events if e["action"] == "contract.sign")
    assert sign["data"]["via"] == "assinatura_eletronica"
    # cronograma nasce na assinatura, como no registro manual: entrada + 10 parcelas
    assert len(client.get(f"{API}/contracts/{c['id']}/installments", headers=h).json()) == 11


def test_rejection_keeps_draft_and_allows_new_send(client, env):
    h = env["h"]
    c = env["contract"]()
    req = _send(client, h, c["id"]).json()
    r = _simulate(client, h, req["id"], "reject", "valor da entrada")
    assert r.json()["result"] == "recusado"
    assert r.json()["request"]["detail"] == "valor da entrada"
    assert client.get(f"{API}/contracts/{c['id']}", headers=h).json()["status"] == "rascunho"
    assert _send(client, h, c["id"]).status_code == 201  # pode reenviar


def test_cancel_request_unfreezes_contract(client, env):
    h = env["h"]
    c = env["contract"]()
    req = _send(client, h, c["id"]).json()
    r = client.post(
        f"{API}/signature-requests/{req['id']}/cancel", headers=h, json={"reason": "enviar corrigido"}
    )
    assert r.status_code == 200 and r.json()["status"] == "cancelado"
    assert (
        client.post(
            f"{API}/signature-requests/{req['id']}/cancel", headers=h, json={"reason": "de novo"}
        ).status_code
        == 409
    )
    assert _simulate(client, h, req["id"]).status_code == 409  # simulador também já cancelou o envelope
    assert (
        client.post(f"{API}/contracts/{c['id']}/sign", headers=h, json={"expected_version": 1}).status_code
        == 200
    )


# ─────────────────────────── webhooks ───────────────────────────


def _row(req):
    """(tenant_id, external_id) do envio, lidos direto no banco de teste."""
    from sqlalchemy import create_engine, text

    from tests.conftest import OWNER_TEST_URL

    eng = create_engine(OWNER_TEST_URL)
    with eng.connect() as c:
        row = c.execute(
            text("SELECT tenant_id, external_id FROM signature_requests WHERE id = :i"), {"i": req["id"]}
        ).one()
    eng.dispose()
    return row


def _sandbox_event(req, etype, reference=None):
    from app.services.esign.sandbox import SIGNATURE_HEADER, sign

    tenant_id, external_id = _row(req)
    body = json.dumps(
        {
            "id": f"evt_{etype}_{req['id'][:8]}",
            "type": etype,
            "envelope_id": external_id,
            "reference": reference or f"{tenant_id}:{req['id']}",
            "signers": [],
        },
        separators=(",", ":"),
    ).encode()
    return body, {SIGNATURE_HEADER: sign(body), "content-type": "application/json"}


def test_webhook_requires_valid_signature(client, env):
    h = env["h"]
    req = _send(client, h, env["contract"]()["id"]).json()
    body, headers = _sandbox_event(req, "completed")
    bad = {**headers, "x-esign-sandbox-signature": headers["x-esign-sandbox-signature"][:-2] + "00"}
    assert client.post(f"{API}/webhooks/esign/sandbox", content=body, headers=bad).status_code == 401
    assert client.post(f"{API}/webhooks/esign/sandbox", content=body).status_code == 401
    assert client.post(f"{API}/webhooks/esign/nao-existe", content=body, headers=headers).status_code == 404


def test_completed_webhook_is_not_trusted_without_provider_confirmation(client, env):
    """Evento "concluído" autêntico, mas o provedor ainda diz pendente: nada muda."""
    h = env["h"]
    c = env["contract"]()
    req = _send(client, h, c["id"]).json()
    body, headers = _sandbox_event(req, "completed")
    r = client.post(f"{API}/webhooks/esign/sandbox", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "nao_confirmado"
    assert client.get(f"{API}/contracts/{c['id']}", headers=h).json()["status"] == "rascunho"
    # reenvio do mesmo evento é deduplicado
    assert (
        client.post(f"{API}/webhooks/esign/sandbox", content=body, headers=headers).json()["status"]
        == "duplicado"
    )


def test_webhook_cannot_reach_other_tenant(client, env):
    """Referência apontando para outra empresa: o envio não existe lá (RLS) e nada acontece."""
    h = env["h"]
    req = _send(client, h, env["contract"]()["id"]).json()
    bob = login(client, "bob", "empresa-b")
    me_b = client.get(f"{API}/me", headers=bob).json()
    body, headers = _sandbox_event(req, "completed", reference=f"{me_b['tenant']['id']}:{req['id']}")
    r = client.post(f"{API}/webhooks/esign/sandbox", content=body, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "ignorado"
    assert client.get(f"{API}/contracts/{req['contract_id']}", headers=h).json()["status"] == "rascunho"


def test_webhook_without_reference_is_ignored(client, env):
    req = _send(client, env["h"], env["contract"]()["id"]).json()
    body, headers = _sandbox_event(req, "completed", reference="documento-de-fora")
    assert (
        client.post(f"{API}/webhooks/esign/sandbox", content=body, headers=headers).json()["status"]
        == "ignorado"
    )


# ─────────────────────────── isolamento e permissões ───────────────────────────


def test_isolation_and_permissions(client, env):
    h = env["h"]
    c = env["contract"]()
    req = _send(client, h, c["id"]).json()
    bob = login(client, "bob", "empresa-b")
    assert client.get(f"{API}/contracts/{c['id']}/signature-requests", headers=bob).status_code == 404
    assert client.get(f"{API}/contracts/{c['id']}/document.pdf", headers=bob).status_code == 404
    assert _send(client, bob, c["id"]).status_code == 404
    assert (
        client.post(
            f"{API}/signature-requests/{req['id']}/cancel", headers=bob, json={"reason": "xyz"}
        ).status_code
        == 404
    )
    assert _simulate(client, bob, req["id"]).status_code == 404

    dave = login(client, "dave")  # comercial: gera contrato, mas não assina → também não envia
    assert client.get(f"{API}/contracts/{c['id']}/signature-requests", headers=dave).status_code == 200
    c2 = env["contract"](lot_idx=1)
    assert _send(client, dave, c2["id"]).status_code == 403
    assert (
        client.post(
            f"{API}/signature-requests/{req['id']}/cancel", headers=dave, json={"reason": "xyz"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{API}/contracts/{c['id']}/signature-requests", headers=login(client, "carol")
        ).status_code
        == 403
    )
    erin = login(client, "erin")  # admin pode
    assert (
        client.post(
            f"{API}/signature-requests/{req['id']}/cancel", headers=erin, json={"reason": "admin"}
        ).status_code
        == 200
    )


# ─────────────────────────── adaptador Documenso (servidor falso) ───────────────────────────


class FakeDocumenso:
    """Imita a API v2 do Documenso: create → distribute → GET envelope."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.status = "PENDING"
        self.external_id_seen: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        assert request.headers["authorization"] == "api_test_token"
        if request.url.path == "/api/v2/envelope/create":
            body = request.content.decode("latin-1")
            assert "application/pdf" in body and "%PDF" in body
            payload = json.loads(
                body.split('name="payload"', 1)[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
            )
            self.external_id_seen = payload["externalId"]
            assert [r["role"] for r in payload["recipients"]] == ["SIGNER", "SIGNER"]
            assert all(f["page"] >= 1 for r in payload["recipients"] for f in r["fields"])
            return httpx.Response(200, json={"id": "envelope_test123"})
        if request.url.path == "/api/v2/envelope/distribute":
            assert json.loads(request.content)["envelopeId"] == "envelope_test123"
            return httpx.Response(200, json={"success": True, "id": "envelope_test123", "recipients": []})
        if request.url.path == "/api/v2/envelope/envelope_test123":
            return httpx.Response(
                200,
                json={
                    "id": "envelope_test123",
                    "status": self.status,
                    "recipients": [
                        {
                            "email": "comprador@example.com",
                            "role": "SIGNER",
                            "signingStatus": "SIGNED" if self.status == "COMPLETED" else "NOT_SIGNED",
                        },
                        {
                            "email": "alice@example.com",
                            "role": "SIGNER",
                            "signingStatus": "SIGNED" if self.status == "COMPLETED" else "NOT_SIGNED",
                        },
                    ],
                },
            )
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture()
def documenso(monkeypatch):
    from app.core.config import get_settings
    from app.services import esign
    from app.services.esign.documenso import DocumensoProvider

    s = get_settings()
    monkeypatch.setattr(s, "documenso_url", "https://assinar.example.test")
    monkeypatch.setattr(s, "documenso_api_token", "api_test_token")
    monkeypatch.setattr(s, "documenso_webhook_secret", "segredo-de-teste-com-mais-de-32-caracteres")
    fake = FakeDocumenso()
    monkeypatch.setattr(esign, "_DOCUMENSO", DocumensoProvider(transport=httpx.MockTransport(fake.handler)))
    return fake


def _documenso_event(fake, event="DOCUMENT_COMPLETED"):
    return json.dumps(
        {
            "event": event,
            "payload": {
                "id": 10,
                "envelopeId": "envelope_test123",
                "externalId": fake.external_id_seen,
                "status": "COMPLETED",
                "recipients": [],
            },
            "createdAt": "2026-11-14T12:00:00.000Z",
        }
    ).encode()


def test_documenso_end_to_end(client, env, documenso):
    h = env["h"]
    providers = client.get(f"{API}/esign/providers", headers=h).json()
    assert [p["name"] for p in providers][0] == "documenso"  # real vem antes do simulador
    c = env["contract"]()
    req = _send(client, h, c["id"]).json()
    assert req["provider"] == "documenso" and req["status"] == "enviado"
    assert documenso.calls[:2] == [
        ("POST", "/api/v2/envelope/create"),
        ("POST", "/api/v2/envelope/distribute"),
    ]
    assert documenso.external_id_seen and documenso.external_id_seen.endswith(f":{req['id']}")

    url = f"{API}/webhooks/esign/documenso"
    secret = {"x-documenso-secret": "segredo-de-teste-com-mais-de-32-caracteres"}
    body = _documenso_event(documenso)
    assert client.post(url, content=body, headers={"x-documenso-secret": "errado"}).status_code == 401
    # o webhook diz "concluído", mas a API do Documenso ainda diz PENDING → não assina
    assert client.post(url, content=body, headers=secret).json()["status"] == "nao_confirmado"
    assert client.get(f"{API}/contracts/{c['id']}", headers=h).json()["status"] == "rascunho"

    documenso.status = "COMPLETED"
    body2 = body.replace(b"12:00:00", b"12:05:00")  # novo envio do provedor (outro carimbo)
    assert client.post(url, content=body2, headers=secret).json()["status"] == "assinado"
    assert client.get(f"{API}/contracts/{c['id']}", headers=h).json()["status"] == "assinado"
    assert ("GET", "/api/v2/envelope/envelope_test123") in documenso.calls
    assert client.post(url, content=body2, headers=secret).json()["status"] == "duplicado"


def test_documenso_webhook_rejected_when_secret_not_configured(client, env, documenso, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "documenso_webhook_secret", None)
    r = client.post(f"{API}/webhooks/esign/documenso", content=b"{}", headers={"x-documenso-secret": ""})
    assert r.status_code in (401, 404)  # sem segredo o provedor nem fica disponível


def test_pdf_is_deterministic():
    """Mesmo contrato, mesmos bytes, mesmo em segundos diferentes (hash enviado = prévia)."""
    import time
    from dataclasses import replace
    from datetime import date
    from decimal import Decimal

    from app.services.contract_pdf import ContractDoc, render

    d = ContractDoc(
        company_name="Loteadora",
        number="C-1",
        version=1,
        customer_name="Comprador",
        customer_document=None,
        broker_name=None,
        development_name="Residencial",
        block_code="A",
        lot_number="01",
        area_m2=Decimal("300"),
        total_price=Decimal("90000"),
        down_payment=Decimal("9000"),
        installments_count=10,
        installment_amount=Decimal("8100"),
        amortization="price",
        interest_monthly_pct=Decimal("1"),
        first_due_date=None,
        adjustment_index="nenhum",
        late_fee_pct=Decimal("2"),
        late_interest_monthly_pct=Decimal("1"),
        notes=None,
        company_signer="Alice",
        issued_on=date(2026, 11, 15),
    )
    a, _ = render(d)
    time.sleep(1.1)
    b, _ = render(d)
    assert a == b
    assert render(replace(d, version=2))[0] != a
