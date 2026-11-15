"""Adaptador Documenso (API v2), pensado para instância auto-hospedada (Community Edition).

Fluxo: POST /envelope/create (multipart: payload JSON + PDF) → POST /envelope/distribute (envia
e-mails aos signatários). A conclusão é confirmada com GET /envelope/{id} — o webhook só avisa.

Webhook: o Documenso envia o segredo configurado no cabeçalho `X-Documenso-Secret` (sem carimbo de
tempo). Por isso: comparação em tempo constante, deduplicação de eventos e confirmação da situação
direto na API antes de qualquer efeito.
"""

import hmac
import json
from collections.abc import Mapping
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.esign.base import (
    EVENT_CANCELLED,
    EVENT_COMPLETED,
    EVENT_OTHER,
    EVENT_REJECTED,
    EVENT_SIGNED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_REJECTED,
    EnvelopeRequest,
    EnvelopeStatus,
    InvalidSignatureError,
    ProviderError,
    ProviderEvent,
    SignerState,
)

SECRET_HEADER = "x-documenso-secret"  # noqa: S105 — nome do cabeçalho, não um segredo

_EVENTS = {
    "DOCUMENT_COMPLETED": EVENT_COMPLETED,
    "DOCUMENT_SIGNED": EVENT_SIGNED,
    "DOCUMENT_RECIPIENT_COMPLETED": EVENT_SIGNED,
    "DOCUMENT_REJECTED": EVENT_REJECTED,
    "DOCUMENT_CANCELLED": EVENT_CANCELLED,
}
_STATUSES = {"PENDING": STATUS_PENDING, "COMPLETED": STATUS_COMPLETED, "REJECTED": STATUS_REJECTED}
_SIGNER = {"SIGNED": "assinou", "REJECTED": "recusou"}

# Campos de assinatura na última página do quadro-resumo (percentuais da página; ver contract_pdf.py)
_FIELDS_Y = {"comprador": 72.0, "empresa": 84.0}


def _signer_states(recipients: Any) -> list[SignerState]:
    out = []
    for r in recipients or []:
        if not isinstance(r, dict) or r.get("role") != "SIGNER":
            continue
        out.append(
            SignerState(
                email=str(r.get("email", "")),
                status=_SIGNER.get(str(r.get("signingStatus")), "pendente"),
                signed_at=r.get("signedAt"),
                rejection_reason=r.get("rejectionReason"),
            )
        )
    return out


class DocumensoProvider:
    name = "documenso"
    label = "Documenso"

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # testes injetam um transporte falso

    def _client(self) -> httpx.Client:
        s = get_settings()
        if not s.documenso_configured:
            raise ProviderError("Assinatura eletrônica não configurada")
        assert s.documenso_url and s.documenso_api_token
        return httpx.Client(
            base_url=s.documenso_url.rstrip("/") + "/api/v2",
            headers={"Authorization": s.documenso_api_token},
            timeout=s.esign_http_timeout_seconds,
            transport=self._transport,
        )

    def _post(self, client: httpx.Client, path: str, **kw: Any) -> dict[str, Any]:
        try:
            r = client.post(path, **kw)
        except httpx.HTTPError:
            raise ProviderError("Não foi possível falar com o serviço de assinatura") from None
        if r.status_code >= 400:
            raise ProviderError(f"Serviço de assinatura recusou a operação ({r.status_code})")
        data = r.json()
        return data if isinstance(data, dict) else {}

    def create_envelope(self, req: EnvelopeRequest) -> str:
        recipients = [
            {
                "email": s.email,
                "name": s.name,
                "role": "SIGNER",
                "signingOrder": i + 1,
                "fields": [
                    {
                        "identifier": 0,
                        "type": "SIGNATURE",
                        "page": req.signature_page,
                        "positionX": 10,
                        "positionY": _FIELDS_Y[s.role],
                        "width": 35,
                        "height": 6,
                    },
                    {
                        "identifier": 0,
                        "type": "DATE",
                        "page": req.signature_page,
                        "positionX": 55,
                        "positionY": _FIELDS_Y[s.role] + 2,
                        "width": 20,
                        "height": 3,
                    },
                ],
            }
            for i, s in enumerate(req.signers)
        ]
        payload = {
            "type": "DOCUMENT",
            "title": req.title,
            "externalId": req.reference,
            "recipients": recipients,
            "meta": {"subject": f"Assinatura: {req.title}", "message": req.message, "language": "pt-BR"},
        }
        with self._client() as c:
            created = self._post(
                c,
                "/envelope/create",
                data={"payload": json.dumps(payload)},
                files={"files": ("contrato.pdf", req.pdf, "application/pdf")},
            )
            envelope_id = str(created.get("id") or "")
            if not envelope_id:
                raise ProviderError("Serviço de assinatura não devolveu o envelope")
            self._post(c, "/envelope/distribute", json={"envelopeId": envelope_id})
        return envelope_id

    def cancel_envelope(self, external_id: str, reason: str) -> None:
        with self._client() as c:
            self._post(c, "/envelope/cancel", json={"envelopeId": external_id, "reason": reason})

    def fetch_status(self, external_id: str) -> EnvelopeStatus:
        with self._client() as c:
            try:
                r = c.get(f"/envelope/{external_id}")
            except httpx.HTTPError:
                raise ProviderError("Não foi possível falar com o serviço de assinatura") from None
            if r.status_code >= 400:
                raise ProviderError(f"Envelope não encontrado no serviço de assinatura ({r.status_code})")
            data = r.json()
        signers = _signer_states(data.get("recipients"))
        status = _STATUSES.get(str(data.get("status")), STATUS_PENDING)
        if status == STATUS_PENDING and any(s.status == "recusou" for s in signers):
            status = STATUS_REJECTED
        if str(data.get("status")) == "CANCELLED":
            status = STATUS_CANCELLED
        return EnvelopeStatus(status=status, signers=signers)

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> None:
        expected = get_settings().documenso_webhook_secret
        received = headers.get(SECRET_HEADER)
        if not expected:
            raise InvalidSignatureError("segredo do webhook não configurado")  # nunca aceitar sem segredo
        if not received or not hmac.compare_digest(received.encode(), expected.encode()):
            raise InvalidSignatureError("segredo inválido")

    def parse_webhook(self, body: bytes) -> ProviderEvent:
        try:
            data = json.loads(body)
            event = str(data["event"])
            payload = data["payload"]
            assert isinstance(payload, dict)
            external_id = str(payload.get("envelopeId") or payload.get("id"))
            reference = payload.get("externalId")
            created = str(data.get("createdAt", ""))
        except (ValueError, KeyError, TypeError, AssertionError):
            raise InvalidSignatureError("evento malformado") from None
        # O Documenso não manda id de evento: deriva um estável para deduplicar reenvios.
        event_id = f"{event}:{external_id}:{created}"[:100]
        return ProviderEvent(
            event_id=event_id,
            type=_EVENTS.get(event, EVENT_OTHER),
            external_id=external_id,
            reference=str(reference) if reference else None,
            signers=_signer_states(payload.get("recipients")),
            raw=data,
        )
