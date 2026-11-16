"""Contrato do adaptador de pagamentos (PSP/banco).

Cada provedor implementa `PaymentProvider`. O resto do sistema só conhece estes tipos — trocar o
sandbox por um PSP real (decisão pendente 6) é escrever outro adaptador, sem mexer em parcelas,
recebimentos ou webhooks.

Regras para qualquer adaptador:
- credenciais vêm do ambiente/cofre, nunca do banco em texto puro nem do Git;
- `verify_webhook` autentica a notificação (assinatura + janela de tempo contra replay) ANTES de
  qualquer leitura do conteúdo; `parse_webhook` só roda depois disso;
- valores sempre em `Decimal`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol

ChargeMethod = Literal["pix", "boleto"]

# Tipos normalizados de evento (cada adaptador traduz os do seu PSP para estes)
EVENT_PAID = "charge.paid"
EVENT_EXPIRED = "charge.expired"
EVENT_CANCELLED = "charge.cancelled"
EVENT_REFUNDED = "charge.refunded"  # devolvido por inteiro ao pagador
EVENT_PARTIALLY_REFUNDED = "charge.partially_refunded"  # parte devolvida (amount = total devolvido)


class ProviderError(Exception):
    """Falha ao falar com o PSP (mensagem pronta para o usuário)."""


class InvalidSignatureError(Exception):
    """Webhook sem assinatura válida, fora da janela de tempo ou malformado."""


@dataclass(frozen=True)
class ChargeRequest:
    reference: str  # id da cobrança no nosso sistema (o PSP devolve nos webhooks)
    method: ChargeMethod
    amount: Decimal
    due_date: date
    payer_name: str
    payer_document: str | None
    description: str
    payer_reference: str | None = None  # id do pagador no nosso sistema (o PSP reaproveita o cadastro)
    # Encargos que o PSP cobra se pago DEPOIS do vencimento (None = o PSP não acrescenta nada)
    late_fee_pct: Decimal | None = None
    late_interest_monthly_pct: Decimal | None = None


@dataclass(frozen=True)
class IssuedCharge:
    external_id: str
    pix_code: str | None = None  # Pix copia e cola
    boleto_line: str | None = None  # linha digitável
    payment_url: str | None = None  # página do PSP com Pix/boleto em PDF para o pagador


@dataclass(frozen=True)
class ProviderEvent:
    event_id: str
    type: str
    external_id: str | None
    amount: Decimal | None = None
    paid_on: date | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    reference: str | None = None  # `ChargeRequest.reference` devolvido pelo PSP (se ele devolver)


class PaymentProvider(Protocol):
    name: str
    label: str

    def create_charge(self, req: ChargeRequest) -> IssuedCharge: ...

    def cancel_charge(self, external_id: str) -> None: ...

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> None:
        """Levanta InvalidSignatureError se a notificação não for autêntica."""
        ...

    def parse_webhook(self, body: bytes) -> ProviderEvent:
        """Só chamar depois de `verify_webhook`. Levanta InvalidSignatureError se malformado
        (e ProviderError se precisar confirmar no PSP e ele não responder — o PSP reenvia)."""
        ...

    def fetch_event(self, external_id: str) -> ProviderEvent | None:
        """Situação atual da cobrança consultada no PSP, como evento (None = nada a aplicar).

        Rede de segurança para webhook perdido: o botão "Atualizar situação" passa pelo mesmo
        processamento de um webhook."""
        ...
