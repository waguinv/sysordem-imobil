"""PSP "sandbox": simula cobranças Pix/boleto sem dinheiro real.

- Códigos Pix e linha digitável são propositalmente INVÁLIDOS para pagamento (marcados SANDBOX / banco
  999), para ninguém confundir com cobrança real.
- Os webhooks são assinados como um PSP real faria: cabeçalho
  `X-Sandbox-Signature: t=<unix>,v1=<hex HMAC-SHA256(segredo, "<t>.<corpo>")>`, com janela de tempo
  (`PAYMENTS_WEBHOOK_TOLERANCE_SECONDS`) contra replay e comparação em tempo constante.
- `build_event` gera um evento assinado; a API usa para "simular pagamento" passando pelo MESMO caminho
  de verificação e processamento de um webhook externo.
"""

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

from app.core.config import get_settings
from app.services.psp.base import (
    ChargeRequest,
    InvalidSignatureError,
    IssuedCharge,
    ProviderEvent,
)

SIGNATURE_HEADER = "x-sandbox-signature"


@lru_cache
def _secret() -> bytes:
    configured = get_settings().payments_sandbox_secret
    # Sem segredo configurado: aleatório por processo (webhooks externos exigem um segredo fixo).
    return (configured or secrets.token_urlsafe(48)).encode()


def sign(body: bytes, timestamp: int | None = None) -> str:
    t = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(_secret(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def _boleto_line(external_id: str) -> str:
    digits = "9990" + str(int(hashlib.sha256(external_id.encode()).hexdigest(), 16))[:43].rjust(43, "0")
    d = digits
    return f"{d[0:5]}.{d[5:10]} {d[10:15]}.{d[15:21]} {d[21:26]}.{d[26:32]} {d[32]} {d[33:47]}"


class SandboxProvider:
    name = "sandbox"
    label = "Sandbox (simulado)"

    def create_charge(self, req: ChargeRequest) -> IssuedCharge:
        external_id = f"sbx_{secrets.token_hex(12)}"
        if req.method == "pix":
            return IssuedCharge(
                external_id=external_id,
                pix_code=f"SANDBOX-PIX-NAO-PAGAVEL|{external_id}|{req.amount}|{req.due_date.isoformat()}",
            )
        return IssuedCharge(external_id=external_id, boleto_line=_boleto_line(external_id))

    def cancel_charge(self, external_id: str) -> None:
        return None  # nada a fazer no simulador

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> None:
        raw = headers.get(SIGNATURE_HEADER)
        if not raw:
            raise InvalidSignatureError("assinatura ausente")
        try:
            parts = dict(p.split("=", 1) for p in raw.split(","))
            t = int(parts["t"])
            received = parts["v1"]
        except (ValueError, KeyError):
            raise InvalidSignatureError("assinatura malformada") from None
        if abs(time.time() - t) > get_settings().payments_webhook_tolerance_seconds:
            raise InvalidSignatureError("carimbo de tempo fora da janela")
        expected = sign(body, t).split("v1=", 1)[1]
        if not hmac.compare_digest(expected, received):
            raise InvalidSignatureError("assinatura inválida")

    def parse_webhook(self, body: bytes) -> ProviderEvent:
        try:
            data = json.loads(body)
            event_id = str(data["id"])
            etype = str(data["type"])
            payload = data.get("data") or {}
            external_id = payload.get("charge_id")
            amount = Decimal(str(payload["amount"])) if payload.get("amount") is not None else None
            paid_on = date.fromisoformat(payload["paid_on"]) if payload.get("paid_on") else None
        except (ValueError, KeyError, TypeError, InvalidOperation):
            raise InvalidSignatureError("evento malformado") from None
        if not event_id or len(event_id) > 100 or len(etype) > 60:
            raise InvalidSignatureError("evento malformado")
        if amount is not None and (amount <= 0 or amount != amount.quantize(Decimal("0.01"))):
            raise InvalidSignatureError("valor inválido")
        return ProviderEvent(
            event_id=event_id,
            type=etype,
            external_id=str(external_id) if external_id else None,
            amount=amount,
            paid_on=paid_on,
            raw=data,
        )

    # ── simulador ──

    def build_event(
        self,
        *,
        etype: str,
        external_id: str,
        amount: Decimal | None = None,
        paid_on: date | None = None,
        event_id: str | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        data: dict[str, Any] = {"charge_id": external_id}
        if amount is not None:
            data["amount"] = str(amount)
        if paid_on is not None:
            data["paid_on"] = paid_on.isoformat()
        body = json.dumps(
            {
                "id": event_id or f"evt_{secrets.token_hex(12)}",
                "type": etype,
                "created_at": int(time.time()),
                "data": data,
            },
            separators=(",", ":"),
        ).encode()
        return body, {SIGNATURE_HEADER: sign(body)}
