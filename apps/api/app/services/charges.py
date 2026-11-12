"""Cobranças Pix/boleto por PSP e processamento de webhooks (Fase 4a).

Princípios:
- A cobrança NÃO é lançamento financeiro. O dinheiro só entra quando o PSP confirma por webhook
  autenticado; aí vira um recebimento normal (`receivables.register_payment`, com multa/mora e
  comissão/repasse como qualquer outro). Redirect/retorno de navegador nunca confirma liquidação.
- Webhook: autenticado pelo adaptador (assinatura + janela de tempo) antes de tudo; idempotente
  (cobrança já paga → ignorado) e deduplicado (mesmo id de evento → "duplicado", sem efeito).
  Duas entregas simultâneas do mesmo evento: a trava da cobrança (FOR UPDATE) serializa e o índice
  único de `webhook_events` barra a segunda.
- Dinheiro nunca é perdido: pagamento confirmado que não pode ser aplicado (parcela já quitada,
  renegociada, valor acima do devido…) deixa a cobrança "divergente" para o financeiro resolver.
- Recebimento manual, renegociação e distrato cancelam a cobrança pendente da parcela.

Ordem de travas: cobrança → parcela (o recebimento manual só trava a parcela), sem ciclos.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.models import Charge, Customer, Installment, SalesContract, WebhookEvent
from app.services import audit, psp
from app.services.psp.base import (
    EVENT_CANCELLED,
    EVENT_EXPIRED,
    EVENT_PAID,
    ChargeMethod,
    ChargeRequest,
    PaymentProvider,
    ProviderError,
)
from app.services.psp.sandbox import SandboxProvider
from app.services.receivables import ReceivableError, quote_installment, register_payment

log = logging.getLogger("imobil.charges")

MAX_DUE_DAYS = 365


class ChargeError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class ChargeNotFoundError(ChargeError):
    pass


@dataclass(frozen=True)
class WebhookOutcome:
    result: str  # processado | ignorado | divergente | duplicado
    detail: str | None = None
    charge_id: uuid.UUID | None = None


def _lock_charge(db: Session, tenant_id: uuid.UUID, charge_id: uuid.UUID) -> Charge:
    c = db.scalar(
        select(Charge).where(Charge.id == charge_id, Charge.tenant_id == tenant_id).with_for_update()
    )
    if c is None:
        raise ChargeNotFoundError("Cobrança não encontrada")
    return c


def _provider_for(c: Charge) -> PaymentProvider:
    p = psp.get_provider(c.provider)
    if p is None:
        raise ChargeError(f"Provedor de pagamento '{c.provider}' não está habilitado neste ambiente")
    return p


# ─────────────────────────── emissão e cancelamento ───────────────────────────


def issue_charge(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    installment_id: uuid.UUID,
    method: ChargeMethod,
    due_date: date | None,
    actor: uuid.UUID | None,
    origin: str = "equipe",
) -> Charge:
    provider = psp.default_provider()
    if provider is None:
        raise ChargeError("Nenhum provedor de pagamento habilitado neste ambiente")
    inst = db.scalar(
        select(Installment)
        .where(Installment.id == installment_id, Installment.tenant_id == tenant_id)
        .with_for_update()
    )
    if inst is None:
        raise ChargeNotFoundError("Parcela não encontrada")
    if inst.status != "aberta":
        raise ChargeError(f"Parcela não está em aberto (situação: {inst.status})")
    today = business_today()
    due = due_date or max(inst.due_date, today)
    if due < today:
        raise ChargeError("Vencimento da cobrança não pode estar no passado")
    if due > today + timedelta(days=MAX_DUE_DAYS):
        raise ChargeError(f"Vencimento da cobrança deve ser em até {MAX_DUE_DAYS} dias")
    pending = db.scalar(
        select(Charge.id).where(Charge.installment_id == inst.id, Charge.status == "pendente")
    )
    if pending is not None:
        raise ChargeError("Já existe cobrança pendente para esta parcela — cancele-a antes de emitir outra")
    contract = db.scalar(select(SalesContract).where(SalesContract.id == inst.contract_id))
    assert contract is not None
    customer = db.scalar(select(Customer).where(Customer.id == contract.customer_id))
    assert customer is not None
    # Valor na data de vencimento da cobrança (com multa/mora se a parcela já estiver atrasada)
    amount = quote_installment(db, inst, due).total

    charge_id = uuid.uuid4()
    label = "Entrada" if inst.number == 0 else f"Parcela {inst.number}"
    try:
        issued = provider.create_charge(
            ChargeRequest(
                reference=str(charge_id),
                method=method,
                amount=amount,
                due_date=due,
                payer_name=customer.name,
                payer_document=customer.document,
                description=f"{label} do contrato {contract.number}",
            )
        )
    except ProviderError as e:
        raise ChargeError(f"PSP recusou a cobrança: {e}") from None
    c = Charge(
        id=charge_id,
        tenant_id=tenant_id,
        installment_id=inst.id,
        contract_id=contract.id,
        customer_id=customer.id,
        provider=provider.name,
        method=method,
        external_id=issued.external_id,
        amount=amount,
        due_date=due,
        status="pendente",
        pix_code=issued.pix_code,
        boleto_line=issued.boleto_line,
        created_by=actor,
    )
    try:
        with db.begin_nested():
            db.add(c)
            db.flush()
    except IntegrityError:
        _safe_cancel(provider, issued.external_id)
        raise ChargeError("Já existe cobrança pendente para esta parcela") from None
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="charge.create",
        entity="installment",
        entity_id=inst.id,
        data={
            "charge_id": str(c.id),
            "provider": c.provider,
            "method": method,
            "amount": str(amount),
            "due_date": due.isoformat(),
            "origin": origin,  # "equipe" ou "portal" (comprador emitiu a 2ª via)
        },
    )
    return c


def _safe_cancel(provider: PaymentProvider, external_id: str) -> bool:
    try:
        provider.cancel_charge(external_id)
        return True
    except ProviderError:
        log.warning("falha ao cancelar cobrança no PSP %s: %s", provider.name, external_id)
        return False


def cancel_charge(
    db: Session, *, tenant_id: uuid.UUID, charge_id: uuid.UUID, reason: str, actor: uuid.UUID
) -> Charge:
    c = _lock_charge(db, tenant_id, charge_id)
    if c.status != "pendente":
        raise ChargeError(f"Só cobrança pendente pode ser cancelada (situação: {c.status})")
    provider = _provider_for(c)
    try:
        provider.cancel_charge(c.external_id)
    except ProviderError as e:
        raise ChargeError(f"PSP não cancelou a cobrança: {e}") from None
    c.status = "cancelada"
    c.status_reason = reason
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="charge.cancel",
        entity="installment",
        entity_id=c.installment_id,
        reason=reason,
        data={"charge_id": str(c.id)},
    )
    return c


def lock_pending(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    installment_ids: list[uuid.UUID] | None = None,
    contract_id: uuid.UUID | None = None,
) -> None:
    """Trava as cobranças pendentes ANTES das parcelas (mesma ordem do webhook: cobrança → parcela)."""
    stmt = select(Charge.id).where(Charge.tenant_id == tenant_id, Charge.status == "pendente")
    if installment_ids is not None:
        stmt = stmt.where(Charge.installment_id.in_(installment_ids))
    if contract_id is not None:
        stmt = stmt.where(Charge.contract_id == contract_id)
    db.execute(stmt.with_for_update())


def cancel_pending_for(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    installment_ids: list[uuid.UUID],
    reason: str,
    actor: uuid.UUID | None,
) -> int:
    """Cancela as cobranças pendentes das parcelas (recebimento manual, renegociação, distrato).

    Falha do PSP não impede: a cobrança fica cancelada aqui e, se ainda assim for paga, o webhook a
    marca como divergente para o financeiro devolver ou aplicar.
    """
    if not installment_ids:
        return 0
    charges = db.scalars(
        select(Charge)
        .where(
            Charge.tenant_id == tenant_id,
            Charge.installment_id.in_(installment_ids),
            Charge.status == "pendente",
        )
        .with_for_update()
    ).all()
    for c in charges:
        p = psp.get_provider(c.provider)
        cancelled_at_psp = _safe_cancel(p, c.external_id) if p else False
        c.status = "cancelada"
        c.status_reason = reason if cancelled_at_psp else f"{reason} (não confirmado no PSP)"
        audit.record(
            db,
            tenant_id=tenant_id,
            actor_user_id=actor,
            action="charge.cancel",
            entity="installment",
            entity_id=c.installment_id,
            reason=c.status_reason,
            data={"charge_id": str(c.id), "automatic": True},
        )
    db.flush()
    return len(charges)


def resolve_charge(
    db: Session, *, tenant_id: uuid.UUID, charge_id: uuid.UUID, reason: str, actor: uuid.UUID
) -> Charge:
    """Divergência tratada fora do sistema (devolução ao pagador, lançamento manual…)."""
    c = _lock_charge(db, tenant_id, charge_id)
    if c.status != "divergente":
        raise ChargeError(f"Só cobrança divergente pode ser resolvida (situação: {c.status})")
    c.status = "resolvida"
    c.status_reason = f"{c.status_reason or ''}\nResolução: {reason}".strip()
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="charge.resolve",
        entity="installment",
        entity_id=c.installment_id,
        reason=reason,
        data={"charge_id": str(c.id), "paid_amount": str(c.paid_amount)},
    )
    return c


# ─────────────────────────── webhooks ───────────────────────────


def process_webhook(
    db: Session, *, tenant_id: uuid.UUID, provider: PaymentProvider, body: bytes
) -> WebhookOutcome:
    """Processa um webhook JÁ AUTENTICADO (`provider.verify_webhook`) no contexto do tenant."""
    ev = provider.parse_webhook(body)
    dedup = (
        WebhookEvent.tenant_id == tenant_id,
        WebhookEvent.provider == provider.name,
        WebhookEvent.event_id == ev.event_id,
    )
    if db.scalar(select(WebhookEvent.id).where(*dedup)) is not None:
        return WebhookOutcome("duplicado", "evento já recebido")

    charge = None
    if ev.external_id:
        charge = db.scalar(
            select(Charge)
            .where(
                Charge.tenant_id == tenant_id,
                Charge.provider == provider.name,
                Charge.external_id == ev.external_id,
            )
            .with_for_update()
        )
    we = WebhookEvent(
        tenant_id=tenant_id,
        provider=provider.name,
        event_id=ev.event_id,
        event_type=ev.type,
        external_id=ev.external_id,
        charge_id=charge.id if charge else None,
        payload=ev.raw,
        result="recebido",
    )
    try:
        with db.begin_nested():
            db.add(we)
            db.flush()
    except IntegrityError:
        return WebhookOutcome("duplicado", "evento já recebido")

    result, detail = _apply(db, tenant_id, provider, charge, ev.type, ev.amount, ev.paid_on, ev.event_id)
    we.result = result
    we.detail = detail
    we.processed_at = datetime.now(UTC)
    db.flush()
    log.info("webhook %s %s → %s", provider.name, ev.type, result)
    return WebhookOutcome(result, detail, charge.id if charge else None)


def _apply(
    db: Session,
    tenant_id: uuid.UUID,
    provider: PaymentProvider,
    charge: Charge | None,
    etype: str,
    amount: Decimal | None,
    paid_on: date | None,
    event_id: str,
) -> tuple[str, str | None]:
    if charge is None:
        return "ignorado", "cobrança não encontrada"

    if etype == EVENT_PAID:
        if charge.status in ("paga", "divergente", "resolvida"):
            return "ignorado", f"cobrança já {charge.status}"
        value = amount or charge.amount
        day = paid_on or business_today()
        try:
            with db.begin_nested():
                p = register_payment(
                    db,
                    tenant_id=tenant_id,
                    installment_id=charge.installment_id,
                    amount=value,
                    received_on=day,
                    method=charge.method,
                    notes=f"Cobrança {provider.label} {charge.external_id}",
                    actor=None,
                )
        except ReceivableError as e:
            charge.paid_amount = value
            charge.paid_on = day
            charge.status = "divergente"
            charge.status_reason = f"Pagamento confirmado pelo PSP, mas não aplicado à parcela: {e}"
            db.flush()
            audit.record(
                db,
                tenant_id=tenant_id,
                actor_user_id=None,
                action="charge.divergent",
                entity="installment",
                entity_id=charge.installment_id,
                reason=str(e),
                data={"charge_id": str(charge.id), "amount": str(value), "event_id": event_id},
            )
            return "divergente", charge.status_reason
        charge.paid_amount = value
        charge.paid_on = day
        charge.payment_id = p.id
        charge.status = "paga"
        charge.status_reason = None
        db.flush()
        audit.record(
            db,
            tenant_id=tenant_id,
            actor_user_id=None,
            action="charge.paid",
            entity="installment",
            entity_id=charge.installment_id,
            data={
                "charge_id": str(charge.id),
                "payment_id": str(p.id),
                "amount": str(value),
                "event_id": event_id,
            },
        )
        return "processado", None

    if etype in (EVENT_EXPIRED, EVENT_CANCELLED):
        if charge.status != "pendente":
            return "ignorado", f"cobrança {charge.status}"
        charge.status = "expirada" if etype == EVENT_EXPIRED else "cancelada"
        charge.status_reason = "Expirada no PSP" if etype == EVENT_EXPIRED else "Cancelada no PSP"
        db.flush()
        audit.record(
            db,
            tenant_id=tenant_id,
            actor_user_id=None,
            action=f"charge.{'expire' if etype == EVENT_EXPIRED else 'cancel'}",
            entity="installment",
            entity_id=charge.installment_id,
            data={"charge_id": str(charge.id), "event_id": event_id},
        )
        return "processado", None

    return "ignorado", f"tipo de evento não tratado: {etype}"


# ─────────────────────────── simulador (sandbox) ───────────────────────────


def simulate(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    charge_id: uuid.UUID,
    event: Literal["paid", "expired"],
    amount: Decimal | None,
    paid_on: date | None,
) -> WebhookOutcome:
    """Gera um webhook ASSINADO do sandbox e o processa pelo mesmo caminho de um webhook externo."""
    c = db.scalar(select(Charge).where(Charge.id == charge_id, Charge.tenant_id == tenant_id))
    if c is None:
        raise ChargeNotFoundError("Cobrança não encontrada")
    if c.provider != "sandbox":
        raise ChargeError("Simulação só existe para cobranças do sandbox")
    provider = psp.get_provider("sandbox")
    if not isinstance(provider, SandboxProvider):
        raise ChargeError("Sandbox de pagamentos desabilitado neste ambiente")
    if event == "paid":
        day = paid_on or business_today()
        if day > business_today():
            raise ChargeError("Data do pagamento não pode estar no futuro")
        if amount is None:
            if day <= c.due_date:
                amount = c.amount
            else:  # pago após o vencimento da cobrança: PSP cobra os encargos do dia
                inst = db.scalar(select(Installment).where(Installment.id == c.installment_id))
                assert inst is not None
                amount = quote_installment(db, inst, day).total
        body, headers = provider.build_event(
            etype=EVENT_PAID, external_id=c.external_id, amount=amount, paid_on=day
        )
    else:
        body, headers = provider.build_event(etype=EVENT_EXPIRED, external_id=c.external_id)
    provider.verify_webhook(headers, body)
    return process_webhook(db, tenant_id=tenant_id, provider=provider, body=body)
