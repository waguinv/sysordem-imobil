"""Simulador de assinatura eletrônica (desenvolvimento e testes; desligado em produção).

- Envelopes ficam num registro em memória do processo; `fetch_status` responde a partir dele, como o
  provedor real responderia — então a conclusão passa pela MESMA confirmação de um envelope real.
- Webhooks assinados com HMAC-SHA256 + carimbo de tempo (mesmo formato do simulador de pagamentos).
"""

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import lru_cache

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

SIGNATURE_HEADER = "x-esign-sandbox-signature"
_TYPES = {EVENT_COMPLETED, EVENT_SIGNED, EVENT_REJECTED, EVENT_CANCELLED}


@lru_cache
def _secret() -> bytes:
    return secrets.token_urlsafe(48).encode()  # por processo: só o próprio simulador assina


def sign(body: bytes, timestamp: int | None = None) -> str:
    t = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(_secret(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


class SandboxSignatureProvider:
    name = "sandbox"
    label = "Simulador (sem validade jurídica)"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._envelopes: dict[str, dict[str, object]] = {}

    def create_envelope(self, req: EnvelopeRequest) -> str:
        if not req.pdf.startswith(b"%PDF"):
            raise ProviderError("Documento inválido")
        external_id = f"sbx_env_{secrets.token_hex(10)}"
        with self._lock:
            self._envelopes[external_id] = {
                "reference": req.reference,
                "status": STATUS_PENDING,
                "signers": {
                    s.email: {"status": "pendente", "signed_at": None, "reason": None} for s in req.signers
                },
            }
        return external_id

    def cancel_envelope(self, external_id: str, reason: str) -> None:
        with self._lock:
            env = self._envelopes.get(external_id)
            if env is None or env["status"] != STATUS_PENDING:
                raise ProviderError("Envelope não está pendente no provedor")
            env["status"] = STATUS_CANCELLED

    def fetch_status(self, external_id: str) -> EnvelopeStatus:
        with self._lock:
            env = self._envelopes.get(external_id)
            if env is None:
                raise ProviderError("Envelope não encontrado no provedor")
            signers = env["signers"]
            assert isinstance(signers, dict)
            return EnvelopeStatus(
                status=str(env["status"]),
                signers=[
                    SignerState(
                        email=e, status=v["status"], signed_at=v["signed_at"], rejection_reason=v["reason"]
                    )
                    for e, v in signers.items()
                ],
            )

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
        if not hmac.compare_digest(sign(body, t).split("v1=", 1)[1], received):
            raise InvalidSignatureError("assinatura inválida")

    def parse_webhook(self, body: bytes) -> ProviderEvent:
        try:
            data = json.loads(body)
            etype = str(data["type"])
            event_id = str(data["id"])
            external_id = str(data["envelope_id"])
            reference = str(data["reference"])
        except (ValueError, KeyError, TypeError):
            raise InvalidSignatureError("evento malformado") from None
        signers = [
            SignerState(
                email=str(s["email"]),
                status=str(s["status"]),
                signed_at=s.get("signed_at"),
                rejection_reason=s.get("reason"),
            )
            for s in data.get("signers", [])
            if isinstance(s, dict) and "email" in s and "status" in s
        ]
        return ProviderEvent(
            event_id=event_id,
            type=etype if etype in _TYPES else EVENT_OTHER,
            external_id=external_id,
            reference=reference,
            signers=signers,
            raw=data,
        )

    # ── simulador: o "signatário" age e o provedor notifica ──

    def simulate(
        self, external_id: str, action: str, reason: str | None = None
    ) -> tuple[bytes, dict[str, str]]:
        """action: "sign_all" | "reject". Atualiza o envelope e devolve o webhook assinado."""
        now = datetime.now(UTC).isoformat()
        with self._lock:
            env = self._envelopes.get(external_id)
            if env is None or env["status"] != STATUS_PENDING:
                raise ProviderError("Envelope não está pendente no simulador")
            signers = env["signers"]
            assert isinstance(signers, dict)
            if action == "sign_all":
                for v in signers.values():
                    v.update(status="assinou", signed_at=now)
                env["status"] = STATUS_COMPLETED
                etype = EVENT_COMPLETED
            elif action == "reject":
                first = next(iter(signers.values()))
                first.update(status="recusou", reason=reason or "recusado no simulador")
                env["status"] = STATUS_REJECTED
                etype = EVENT_REJECTED
            else:
                raise ProviderError("Ação desconhecida")
            payload = {
                "id": f"evt_{secrets.token_hex(10)}",
                "type": etype,
                "envelope_id": external_id,
                "reference": env["reference"],
                "signers": [{"email": e, **v} for e, v in signers.items()],
            }
        body = json.dumps(payload, separators=(",", ":"), default=str).encode()
        return body, {SIGNATURE_HEADER: sign(body)}
