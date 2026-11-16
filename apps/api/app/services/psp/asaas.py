"""Adaptador Asaas (API v3) — Pix e boleto reais.

Emissão: cliente (reaproveitado por `externalReference`) → POST /payments → Pix copia e cola
(GET /payments/{id}/pixQrCode) ou linha digitável (GET /payments/{id}/identificationField). Se a 2ª
etapa falhar, a cobrança é apagada no Asaas: nada fica emitido sem estar registrado aqui.

Webhook: o Asaas envia o token configurado no cabeçalho `asaas-access-token` (sem assinatura do corpo
nem carimbo de tempo). Por isso: token comparado por hash em tempo constante, deduplicação por id do
evento e — antes de qualquer efeito — a situação e o valor são CONFIRMADOS direto na API. Um webhook
forjado ou reenviado não paga nada que o Asaas não confirme.

Encargos: o Asaas só acrescenta multa/juros quando a cobrança é emitida até o vencimento da parcela
(mesmas taxas do contrato). Cobrança de parcela já atrasada sai com o valor do dia e sem encargos do
Asaas (o valor já os inclui) — sempre explícito, para a configuração global da conta não somar nada
que a baixa da parcela não espera.
"""

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.psp.base import (
    EVENT_CANCELLED,
    EVENT_PAID,
    ChargeRequest,
    InvalidSignatureError,
    IssuedCharge,
    ProviderError,
    ProviderEvent,
)

log = logging.getLogger("imobil.psp.asaas")

TOKEN_HEADER = "asaas-access-token"  # noqa: S105 — nome do cabeçalho, não um segredo
BASE_URLS = {
    "sandbox": "https://api-sandbox.asaas.com/v3",
    "production": "https://api.asaas.com/v3",
}
USER_AGENT = "SysOrdemImobil/1.0"
WEBHOOK_EVENTS = ["PAYMENT_RECEIVED", "PAYMENT_CONFIRMED", "PAYMENT_DELETED", "PAYMENT_REFUNDED"]
_PAID = {"RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"}
# Testes: um httpx.MockTransport que imita o Asaas (nunca definido fora dos testes)
TRANSPORT_OVERRIDE: httpx.BaseTransport | None = None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AsaasNotFoundError(ProviderError):
    pass


class AsaasClient:
    """Chamadas HTTP cruas. `transport` permite testes sem rede (httpx.MockTransport)."""

    def __init__(
        self, api_key: str, environment: str, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        if environment not in BASE_URLS:
            raise ValueError(f"ambiente Asaas desconhecido: {environment}")
        self.environment = environment
        self._http = httpx.Client(
            base_url=BASE_URLS[environment],
            headers={"access_token": api_key, "User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=get_settings().asaas_http_timeout_seconds,
            transport=transport or TRANSPORT_OVERRIDE,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            r = self._http.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as e:
            log.warning("asaas %s %s falhou: %s", method, path, type(e).__name__)
            raise ProviderError("Asaas indisponível no momento — tente de novo em instantes") from None
        if r.status_code == 401:
            raise ProviderError("Chave de API do Asaas inválida (ou de outro ambiente: sandbox × produção)")
        if r.status_code == 404:
            raise AsaasNotFoundError("não encontrado no Asaas")
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        if r.status_code >= 400:
            errors = data.get("errors") if isinstance(data, dict) else None
            msgs = [str(e.get("description", "")) for e in errors or [] if isinstance(e, dict)]
            msg = "; ".join(m for m in msgs if m)[:300] or f"erro HTTP {r.status_code}"
            raise ProviderError(msg)
        if not isinstance(data, dict):
            raise ProviderError("resposta inesperada do Asaas")
        return data

    # ── conta e webhooks (configuração da empresa) ──

    def account_name(self) -> str | None:
        """Valida a chave (401 → ProviderError) e devolve o nome da conta, se o Asaas informar."""
        try:
            info = self.request("GET", "/myAccount/commercialInfo")
            name = info.get("companyName") or info.get("name")
            return str(name)[:160] if name else None
        except AsaasNotFoundError:
            self.request("GET", "/finance/balance")
            return None

    def create_webhook(self, *, url: str, token: str, email: str) -> str:
        data = self.request(
            "POST",
            "/webhooks",
            json_body={
                "name": "SysOrdem Imobil",
                "url": url,
                "email": email,
                "enabled": True,
                "interrupted": False,
                "apiVersion": 3,
                "authToken": token,
                "sendType": "SEQUENTIALLY",
                "events": WEBHOOK_EVENTS,
            },
        )
        return str(data.get("id") or "")

    def delete_webhook(self, webhook_id: str) -> None:
        try:
            self.request("DELETE", f"/webhooks/{webhook_id}")
        except AsaasNotFoundError:
            return None

    def sandbox_confirm(self, payment_id: str) -> dict[str, Any]:
        """Só no sandbox do Asaas: simula o pagamento (testes ponta a ponta)."""
        if self.environment != "sandbox":
            raise ProviderError("confirmação simulada só existe no sandbox do Asaas")
        return self.request("POST", f"/sandbox/payment/{payment_id}/confirm")


def _money(v: Any) -> Decimal | None:
    if v is None:
        return None
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    return d if d > 0 else None


def _day(v: Any) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


class AsaasProvider:
    name = "asaas"

    def __init__(self, client: AsaasClient, *, webhook_token_sha256: str | None) -> None:
        self.client = client
        self.label = "Asaas" if client.environment == "production" else "Asaas (sandbox)"
        self._token_sha256 = webhook_token_sha256

    @property
    def is_sandbox(self) -> bool:
        return self.client.environment == "sandbox"

    # ── emissão ──

    def _customer_id(self, req: ChargeRequest) -> str:
        doc = "".join(ch for ch in (req.payer_document or "") if ch.isdigit())
        if len(doc) not in (11, 14):
            raise ProviderError("o Asaas exige CPF ou CNPJ do pagador — complete o cadastro antes de emitir")
        if req.payer_reference:
            found = self.client.request(
                "GET", "/customers", params={"externalReference": req.payer_reference, "limit": 1}
            )
            for c in found.get("data") or []:
                if isinstance(c, dict) and c.get("id") and not c.get("deleted"):
                    return str(c["id"])
        created = self.client.request(
            "POST",
            "/customers",
            json_body={
                "name": req.payer_name[:100],
                "cpfCnpj": doc,
                "externalReference": req.payer_reference,
                "notificationDisabled": True,  # avisos ao comprador saem do nosso sistema, não do Asaas
            },
        )
        if not created.get("id"):
            raise ProviderError("Asaas não devolveu o cadastro do pagador")
        return str(created["id"])

    def create_charge(self, req: ChargeRequest) -> IssuedCharge:
        body: dict[str, Any] = {
            "customer": self._customer_id(req),
            "billingType": "PIX" if req.method == "pix" else "BOLETO",
            "value": float(req.amount),
            "dueDate": req.due_date.isoformat(),
            "description": req.description[:500],
            "externalReference": req.reference,
            "fine": {"value": float(req.late_fee_pct or 0), "type": "PERCENTAGE"},
            "interest": {"value": float(req.late_interest_monthly_pct or 0)},
        }
        pay = self.client.request("POST", "/payments", json_body=body)
        pid = str(pay.get("id") or "")
        if not pid:
            raise ProviderError("Asaas não devolveu o id da cobrança")
        try:
            if req.method == "pix":
                qr = self.client.request("GET", f"/payments/{pid}/pixQrCode")
                pix, line = str(qr.get("payload") or ""), None
                if not pix:
                    raise ProviderError("Asaas não devolveu o Pix copia e cola")
            else:
                f = self.client.request("GET", f"/payments/{pid}/identificationField")
                pix, line = None, str(f.get("identificationField") or "")[:80]
                if not line:
                    raise ProviderError("Asaas não devolveu a linha digitável")
        except ProviderError:
            try:
                self.cancel_charge(pid)
            except ProviderError:
                log.warning("cobrança %s ficou emitida no Asaas sem Pix/boleto", pid)
            raise
        url = pay.get("invoiceUrl") or pay.get("bankSlipUrl")
        return IssuedCharge(
            external_id=pid, pix_code=pix, boleto_line=line, payment_url=str(url) if url else None
        )

    def cancel_charge(self, external_id: str) -> None:
        try:
            self.client.request("DELETE", f"/payments/{external_id}")
        except AsaasNotFoundError:
            return None  # já não existe no Asaas

    # ── webhooks ──

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> None:
        received = headers.get(TOKEN_HEADER)
        if not self._token_sha256:
            raise InvalidSignatureError("webhook do Asaas não configurado")
        if not received:
            raise InvalidSignatureError("token ausente")
        if not hmac.compare_digest(token_hash(received), self._token_sha256):
            raise InvalidSignatureError("token inválido")

    def parse_webhook(self, body: bytes) -> ProviderEvent:
        try:
            data = json.loads(body)
            event_id = str(data["id"])
            etype = str(data["event"])
            payment = data.get("payment") or {}
            pid = str(payment["id"]) if isinstance(payment, dict) and payment.get("id") else None
        except (ValueError, KeyError, TypeError):
            raise InvalidSignatureError("evento malformado") from None
        if not event_id or len(event_id) > 100 or len(etype) > 60 or (pid and len(pid) > 100):
            raise InvalidSignatureError("evento malformado")
        ref = payment.get("externalReference") if isinstance(payment, dict) else None
        base = ProviderEvent(
            event_id=event_id,
            type=f"asaas.{etype.lower()}",
            external_id=pid,
            raw=data,
            reference=str(ref)[:100] if ref else None,
        )
        if pid is None or etype not in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED", "PAYMENT_DELETED"):
            return base
        # Confirmação na API: o efeito vem do que o Asaas diz AGORA, não do corpo recebido
        current: dict[str, Any] | None
        try:
            current = self.client.request("GET", f"/payments/{pid}")
        except AsaasNotFoundError:
            current = {"id": pid, "deleted": True} if etype == "PAYMENT_DELETED" else None
        if current is None:
            return base
        confirmed = self._event_from(current, event_id=event_id, raw=data)
        if confirmed is None:
            return base
        return confirmed

    def _event_from(self, p: dict[str, Any], *, event_id: str, raw: dict[str, Any]) -> ProviderEvent | None:
        pid = str(p.get("id") or "")
        ref = p.get("externalReference")
        common = {
            "event_id": event_id,
            "external_id": pid,
            "raw": raw,
            "reference": str(ref)[:100] if ref else None,
        }
        if p.get("deleted"):
            return ProviderEvent(type=EVENT_CANCELLED, **common)  # type: ignore[arg-type]
        if str(p.get("status")) in _PAID:
            amount = _money(p.get("value"))
            if amount is None:
                raise InvalidSignatureError("valor inválido")
            paid_on = (
                _day(p.get("clientPaymentDate")) or _day(p.get("paymentDate")) or _day(p.get("confirmedDate"))
            )
            return ProviderEvent(type=EVENT_PAID, amount=amount, paid_on=paid_on, **common)  # type: ignore[arg-type]
        return None

    def fetch_event(self, external_id: str) -> ProviderEvent | None:
        try:
            p = self.client.request("GET", f"/payments/{external_id}")
        except AsaasNotFoundError:
            p = {"id": external_id, "deleted": True}
        status = "deleted" if p.get("deleted") else str(p.get("status") or "").lower()
        summary = {"source": "consulta", "status": status, "value": p.get("value")}
        return self._event_from(p, event_id=f"consulta:{external_id}:{status}"[:100], raw=summary)
