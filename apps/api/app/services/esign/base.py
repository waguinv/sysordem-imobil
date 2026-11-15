"""Contrato do adaptador de assinatura eletrônica.

O resto do sistema só conhece estes tipos — trocar Documenso por outro provedor é escrever outro
adaptador. Regras para qualquer adaptador:
- credenciais vêm do ambiente/cofre, nunca do banco nem do Git;
- `verify_webhook` autentica a notificação ANTES de qualquer leitura do conteúdo;
- a conclusão NUNCA é aceita só pelo webhook: `fetch_status` confirma direto no provedor antes de
  assinar o contrato (o webhook é só o aviso de que algo mudou).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

SignerRole = Literal["comprador", "empresa"]

# Tipos normalizados de evento
EVENT_COMPLETED = "completed"  # todos assinaram
EVENT_SIGNED = "signed"  # uma assinatura (parcial)
EVENT_REJECTED = "rejected"
EVENT_CANCELLED = "cancelled"
EVENT_OTHER = "other"

# Situação normalizada do envelope no provedor
STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"


class ProviderError(Exception):
    """Falha ao falar com o provedor (mensagem pronta para o usuário)."""


class InvalidSignatureError(Exception):
    """Webhook não autenticado ou malformado."""


@dataclass(frozen=True)
class Signer:
    name: str
    email: str
    role: SignerRole


@dataclass(frozen=True)
class EnvelopeRequest:
    reference: str  # "<tenant_id>:<signature_request_id>" — volta nos webhooks
    title: str
    pdf: bytes
    signers: list[Signer]
    message: str
    signature_page: int = 1  # página (1-based) onde ficam os campos de assinatura


@dataclass(frozen=True)
class SignerState:
    email: str
    status: str  # "pendente" | "assinou" | "recusou"
    signed_at: str | None = None
    rejection_reason: str | None = None


@dataclass(frozen=True)
class EnvelopeStatus:
    status: str  # STATUS_*
    signers: list[SignerState] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    type: str  # EVENT_*
    external_id: str | None
    reference: str | None
    signers: list[SignerState] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class SignatureProvider(Protocol):
    name: str
    label: str

    def create_envelope(self, req: EnvelopeRequest) -> str:
        """Cria e ENVIA o documento aos signatários. Devolve o id externo do envelope."""
        ...

    def cancel_envelope(self, external_id: str, reason: str) -> None: ...

    def fetch_status(self, external_id: str) -> EnvelopeStatus:
        """Situação atual no provedor (fonte da verdade para concluir)."""
        ...

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> None: ...

    def parse_webhook(self, body: bytes) -> ProviderEvent: ...
