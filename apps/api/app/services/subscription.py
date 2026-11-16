"""Assinatura SaaS da empresa (Fase 5c). Valores e prazos são HIPÓTESES (decisão 37).

Situação (calculada, nunca gravada):
- "sem_assinatura": empresa criada antes da 5c ou pelo operador — sem limites nem bloqueio;
- "teste": dentro dos dias grátis;
- "ativa": coberta por fatura paga (ou teste) até `covered_until`;
- "em_atraso": venceu, mas está dentro da carência (GRACE_DAYS) — tudo funciona, com aviso;
- "suspensa": passou da carência — leitura liberada, ALTERAÇÕES bloqueadas (exceto assinatura);
- "cancelada": cancelada e o período pago acabou — idem suspensa.

Faturas: mensais, geradas sob demanda (ao consultar a assinatura e ao gravar) a partir de
INVOICE_LEAD_DAYS antes do fim da cobertura. Pagas por Pix/boleto no PSP da plataforma; a confirmação
só vem por webhook autenticado e idempotente (como na Fase 4a).

O PSP da PLATAFORMA (que cobra a assinatura) é separado do PSP das empresas (que cobra os compradores):
o sandbox da assinatura só liga com BILLING_SANDBOX_ENABLED (proibido em produção) — ligar o sandbox das
empresas (PAYMENTS_SANDBOX_ENABLED) nunca permite "pagar" a própria assinatura pelo simulador.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.config import get_settings
from app.models import (
    BillingEvent,
    Development,
    Lot,
    Membership,
    Plan,
    SubscriptionInvoice,
    Tenant,
    TenantSubscription,
)
from app.services import audit
from app.services.finance import add_months
from app.services.psp.asaas import AsaasClient, AsaasProvider, token_hash
from app.services.psp.base import EVENT_PAID, ChargeRequest, PaymentProvider, ProviderError, ProviderEvent
from app.services.psp.sandbox import SandboxProvider

log = logging.getLogger("imobil.subscription")

TRIAL_DAYS = 14
GRACE_DAYS = 10
INVOICE_LEAD_DAYS = 7
WRITABLE = {"sem_assinatura", "teste", "ativa", "em_atraso"}


class SubscriptionError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class LimitError(SubscriptionError):
    pass


class NotFoundError(SubscriptionError):
    pass


@dataclass(frozen=True)
class Status:
    state: str
    plan: Plan | None
    trial_ends_at: date | None
    covered_until: date | None  # exclusivo: coberto até o dia anterior
    grace_until: date | None
    cancelled: bool

    @property
    def can_write(self) -> bool:
        return self.state in WRITABLE


def get_subscription(db: Session, tenant_id: uuid.UUID) -> TenantSubscription | None:
    return db.get(TenantSubscription, tenant_id)


_SANDBOX = SandboxProvider()


def billing_provider(name: str | None = None) -> PaymentProvider | None:
    """PSP da plataforma: a conta Asaas da SysOrdem (BILLING_ASAAS_*), se configurada; senão o sandbox
    (fora de produção). Uma fatura já emitida sempre volta ao provedor gravado nela."""
    s = get_settings()
    if s.billing_asaas_configured and name in (None, "asaas"):
        assert s.billing_asaas_api_key and s.billing_asaas_webhook_token
        return AsaasProvider(
            AsaasClient(s.billing_asaas_api_key, s.billing_asaas_environment),
            webhook_token_sha256=token_hash(s.billing_asaas_webhook_token),
        )
    if s.billing_sandbox and name in (None, _SANDBOX.name):
        return _SANDBOX
    return None


def invoice_reference(tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> str:
    """Referência enviada ao PSP: o webhook único da conta da plataforma descobre a empresa por ela."""
    return f"{tenant_id}:{invoice_id}"


def tenant_from_reference(reference: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(reference.split(":", 1)[0]) if reference and ":" in reference else None
    except ValueError:
        return None


def can_write(db: Session, tenant_id: uuid.UUID, today: date | None = None) -> bool:
    """Versão barata de `status(...).can_write` para a checagem de cada alteração (1–2 consultas)."""
    sub = get_subscription(db, tenant_id)
    if sub is None:
        return True
    cu = covered_until(db, sub)
    on = today or business_today()
    if on < cu:
        return True
    return sub.cancelled_at is None and on < cu + timedelta(days=GRACE_DAYS)


def covered_until(db: Session, sub: TenantSubscription) -> date:
    paid = db.scalar(
        select(func.max(SubscriptionInvoice.period_end)).where(
            SubscriptionInvoice.tenant_id == sub.tenant_id, SubscriptionInvoice.status == "paga"
        )
    )
    return max(paid or sub.trial_ends_at, sub.trial_ends_at)


def status(db: Session, tenant_id: uuid.UUID, today: date | None = None) -> Status:
    sub = get_subscription(db, tenant_id)
    if sub is None:
        return Status("sem_assinatura", None, None, None, None, False)
    on = today or business_today()
    plan = db.get(Plan, sub.plan_code)
    cu = covered_until(db, sub)
    grace = cu + timedelta(days=GRACE_DAYS)
    has_paid = cu > sub.trial_ends_at
    if on < cu:
        state = "ativa" if has_paid else "teste"
    elif sub.cancelled_at is not None:
        state = "cancelada"
    elif on < grace:
        state = "em_atraso"
    else:
        state = "suspensa"
    return Status(state, plan, sub.trial_ends_at, cu, grace, sub.cancelled_at is not None)


def sync_invoices(db: Session, tenant_id: uuid.UUID, today: date | None = None) -> None:
    """Gera a fatura do próximo período quando faltam INVOICE_LEAD_DAYS para acabar a cobertura."""
    sub = get_subscription(db, tenant_id)
    if sub is None or sub.cancelled_at is not None:
        return
    on = today or business_today()
    cu = covered_until(db, sub)
    if on < cu - timedelta(days=INVOICE_LEAD_DAYS):
        return
    exists = db.scalar(
        select(SubscriptionInvoice.id).where(
            SubscriptionInvoice.tenant_id == tenant_id,
            SubscriptionInvoice.period_start == cu,
            SubscriptionInvoice.status != "cancelada",
        )
    )
    if exists is not None:
        return
    plan = db.get(Plan, sub.plan_code)
    assert plan is not None
    inv = SubscriptionInvoice(
        tenant_id=tenant_id,
        plan_code=plan.code,
        period_start=cu,
        period_end=add_months(cu, 1),
        due_date=cu,
        amount=plan.monthly_price,
        status="aberta",
    )
    try:
        with db.begin_nested():
            db.add(inv)
            db.flush()
    except IntegrityError:
        return  # outra requisição gerou ao mesmo tempo


# ─────────────────────────── limites ───────────────────────────


@dataclass(frozen=True)
class Usage:
    developments: int
    lots: int
    users: int


def usage(db: Session, tenant_id: uuid.UUID) -> Usage:
    return Usage(
        developments=db.scalar(select(func.count()).where(Development.tenant_id == tenant_id)) or 0,
        lots=db.scalar(select(func.count()).where(Lot.tenant_id == tenant_id)) or 0,
        users=db.scalar(
            select(func.count()).where(Membership.tenant_id == tenant_id, Membership.is_active.is_(True))
        )
        or 0,
    )


LIMIT_LABEL = {"developments": "empreendimentos", "lots": "lotes", "users": "usuários ativos"}


def _limit(plan: Plan, kind: str) -> int | None:
    return {"developments": plan.max_developments, "lots": plan.max_lots, "users": plan.max_users}[kind]


def check_limit(db: Session, tenant_id: uuid.UUID, kind: str, adding: int = 1) -> None:
    """LimitError se a inclusão passar do limite do plano. Sem assinatura: sem limite.

    Trava a assinatura (FOR UPDATE) até o fim da transação: duas inclusões simultâneas não passam juntas
    do limite (a segunda espera e conta a primeira).
    """
    sub = db.get(TenantSubscription, tenant_id, with_for_update=True)
    if sub is None:
        return
    plan = db.get(Plan, sub.plan_code)
    assert plan is not None
    limit = _limit(plan, kind)
    if limit is None:
        return
    current = getattr(usage(db, tenant_id), kind)
    if current + adding > limit:
        raise LimitError(
            f"O plano {plan.name} permite até {limit} {LIMIT_LABEL[kind]} (hoje: {current}). "
            "Mude de plano em Configurações → Assinatura."
        )


# ─────────────────────────── cadastro, plano e cancelamento ───────────────────────────


def start(
    db: Session, *, tenant_id: uuid.UUID, plan_code: str, actor: uuid.UUID | None
) -> TenantSubscription:
    plan = db.get(Plan, plan_code)
    if plan is None or not plan.is_public:
        raise SubscriptionError("Plano inválido")
    sub = TenantSubscription(
        tenant_id=tenant_id, plan_code=plan.code, trial_ends_at=business_today() + timedelta(days=TRIAL_DAYS)
    )
    db.add(sub)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="subscription.start",
        entity="subscription",
        data={"plan": plan.code, "trial_ends_at": sub.trial_ends_at.isoformat()},
    )
    return sub


def change_plan(
    db: Session, *, tenant_id: uuid.UUID, plan_code: str, actor: uuid.UUID, reason: str
) -> Status:
    sub = db.get(TenantSubscription, tenant_id, with_for_update=True)
    if sub is None:
        raise SubscriptionError("Esta empresa não tem assinatura")
    plan = db.get(Plan, plan_code)
    if plan is None or not plan.is_public:
        raise SubscriptionError("Plano inválido")
    if plan.code == sub.plan_code:
        raise SubscriptionError("A empresa já está neste plano")
    u = usage(db, tenant_id)
    over = [
        f"{getattr(u, k)} {LIMIT_LABEL[k]} (máx. {_limit(plan, k)})"
        for k in ("developments", "lots", "users")
        if _limit(plan, k) is not None and getattr(u, k) > (_limit(plan, k) or 0)
    ]
    if over:
        raise SubscriptionError(f"O uso atual não cabe no plano {plan.name}: " + "; ".join(over))
    before = sub.plan_code
    sub.plan_code = plan.code
    # fatura em aberto ainda não paga passa a valer o novo preço (vale para o próximo período)
    for inv in db.scalars(
        select(SubscriptionInvoice)
        .where(
            SubscriptionInvoice.tenant_id == tenant_id,
            SubscriptionInvoice.status == "aberta",
            SubscriptionInvoice.external_id.is_(None),
        )
        .with_for_update()
    ).all():
        inv.plan_code = plan.code
        inv.amount = plan.monthly_price
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="subscription.change_plan",
        entity="subscription",
        reason=reason,
        data={"before": before, "after": plan.code},
    )
    return status(db, tenant_id)


def cancel(db: Session, *, tenant_id: uuid.UUID, actor: uuid.UUID, reason: str) -> Status:
    sub = db.get(TenantSubscription, tenant_id, with_for_update=True)
    if sub is None or sub.cancelled_at is not None:
        raise SubscriptionError("Assinatura inexistente ou já cancelada")
    sub.cancelled_at = datetime.now(UTC)
    sub.cancel_reason = reason
    for inv in db.scalars(
        select(SubscriptionInvoice)
        .where(SubscriptionInvoice.tenant_id == tenant_id, SubscriptionInvoice.status == "aberta")
        .with_for_update()
    ).all():
        _cancel_invoice(inv)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="subscription.cancel",
        entity="subscription",
        reason=reason,
    )
    return status(db, tenant_id)


def _cancel_invoice(inv: SubscriptionInvoice) -> None:
    """Cancela a fatura e, se o Pix/boleto já foi emitido, a cobrança no PSP. Se o PSP falhar, a fatura
    fica cancelada aqui e um pagamento que ainda chegue é tratado em `process_event` (nada se perde)."""
    inv.status = "cancelada"
    if inv.external_id is None:
        return
    p = billing_provider(inv.provider)
    try:
        if p is None:
            raise ProviderError("provedor indisponível")
        p.cancel_charge(inv.external_id)
    except ProviderError:
        log.warning("cobrança da fatura %s não foi cancelada no PSP", inv.id)


def reactivate(db: Session, *, tenant_id: uuid.UUID, actor: uuid.UUID) -> Status:
    sub = db.get(TenantSubscription, tenant_id, with_for_update=True)
    if sub is None or sub.cancelled_at is None:
        raise SubscriptionError("A assinatura não está cancelada")
    sub.cancelled_at = None
    sub.cancel_reason = None
    db.flush()
    audit.record(
        db, tenant_id=tenant_id, actor_user_id=actor, action="subscription.reactivate", entity="subscription"
    )
    sync_invoices(db, tenant_id)
    return status(db, tenant_id)


# ─────────────────────────── pagamento das faturas ───────────────────────────


def pay(
    db: Session, *, tenant_id: uuid.UUID, invoice_id: uuid.UUID, method: str, actor: uuid.UUID
) -> SubscriptionInvoice:
    """Emite (uma vez) o Pix/boleto da fatura no PSP da plataforma."""
    inv = db.scalar(
        select(SubscriptionInvoice)
        .where(SubscriptionInvoice.id == invoice_id, SubscriptionInvoice.tenant_id == tenant_id)
        .with_for_update()
    )
    if inv is None:
        raise NotFoundError("Fatura não encontrada")
    if inv.status != "aberta":
        raise SubscriptionError(f"Fatura não está em aberto (situação: {inv.status})")
    if inv.external_id is not None:
        return inv  # já emitida: devolve a mesma
    provider = billing_provider()
    if provider is None:
        raise SubscriptionError("Pagamento da assinatura indisponível neste ambiente")
    tenant = db.get(Tenant, tenant_id)
    assert tenant is not None
    if provider.name != _SANDBOX.name and not tenant.document:
        raise SubscriptionError(
            "Informe o CNPJ da empresa (Configurações → Recebimentos) para gerar a fatura"
        )
    due = max(inv.due_date, business_today())
    try:
        issued = provider.create_charge(
            ChargeRequest(
                reference=invoice_reference(tenant_id, inv.id),
                method="boleto" if method == "boleto" else "pix",
                amount=inv.amount,
                due_date=due,
                payer_name=tenant.name,
                payer_document=tenant.document,
                payer_reference=f"empresa:{tenant_id}",
                description=f"Assinatura SysOrdem Imobil {inv.period_start:%m/%Y}",
            )
        )
    except ProviderError as e:
        raise SubscriptionError(f"PSP recusou a cobrança: {e}") from None
    inv.provider = provider.name
    inv.method = "boleto" if method == "boleto" else "pix"
    inv.external_id = issued.external_id
    inv.pix_code = issued.pix_code
    inv.boleto_line = issued.boleto_line
    inv.payment_url = issued.payment_url
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="subscription_invoice.charge",
        entity="subscription_invoice",
        entity_id=inv.id,
        data={"amount": str(inv.amount), "method": inv.method},
    )
    return inv


def process_event(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    provider: PaymentProvider,
    body: bytes = b"",
    event: ProviderEvent | None = None,
) -> str:
    """Webhook JÁ AUTENTICADO do PSP da plataforma. Idempotente por (empresa, provedor, evento).

    As mudanças na fatura são gravadas FORA do savepoint do evento: um erro inesperado derruba a
    transação (o PSP reenvia), em vez de virar um "duplicado" que esconde um pagamento.
    """
    ev = event or provider.parse_webhook(body)
    if db.scalar(
        select(BillingEvent.id).where(
            BillingEvent.tenant_id == tenant_id,
            BillingEvent.provider == provider.name,
            BillingEvent.event_id == ev.event_id,
        )
    ):
        return "duplicado"
    inv = None
    if ev.external_id:
        inv = db.scalar(
            select(SubscriptionInvoice)
            .where(
                SubscriptionInvoice.tenant_id == tenant_id,
                SubscriptionInvoice.provider == provider.name,
                SubscriptionInvoice.external_id == ev.external_id,
            )
            .with_for_update()
        )
    if inv is None or ev.type != EVENT_PAID:
        result = "ignorado"
    else:
        result = _apply_payment(db, tenant_id, inv, ev.amount, ev.event_id)
    try:
        with db.begin_nested():
            db.add(
                BillingEvent(
                    tenant_id=tenant_id,
                    provider=provider.name,
                    event_id=ev.event_id,
                    event_type=ev.type,
                    invoice_id=inv.id if inv else None,
                    result=result,
                    payload=ev.raw,
                )
            )
            db.flush()
    except IntegrityError:
        # entrega simultânea do MESMO evento: a outra transação travou a fatura antes e já a
        # processou (aqui ela foi vista como paga, sem alteração) — nada a desfazer
        return "duplicado"
    return result


def _divergent(
    db: Session,
    tenant_id: uuid.UUID,
    inv: SubscriptionInvoice,
    note: str,
    amount: Decimal | None,
    event_id: str,
) -> None:
    inv.divergence = note
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=None,
        action="subscription_invoice.divergent",
        entity="subscription_invoice",
        entity_id=inv.id,
        reason=note,
        data={"amount": str(amount) if amount is not None else None, "event_id": event_id},
    )


def _apply_payment(
    db: Session, tenant_id: uuid.UUID, inv: SubscriptionInvoice, amount: Decimal | None, event_id: str
) -> str:
    if inv.status == "paga":
        return "ja_paga"
    today = f"{business_today():%d/%m/%Y}"
    if amount is None or amount < inv.amount:
        _divergent(
            db,
            tenant_id,
            inv,
            f"Pagamento de R$ {amount if amount is not None else '?'} confirmado pelo PSP em {today}, "
            "abaixo do "
            f"valor da fatura (R$ {inv.amount}). A fatura continua em aberto — fale com o suporte.",
            amount,
            event_id,
        )
        return "valor_divergente"
    revived = inv.status == "cancelada"
    if revived:
        # pago depois de cancelada: o dinheiro entrou — honra o pagamento, sem duplicar o período
        other = db.scalar(
            select(SubscriptionInvoice)
            .where(
                SubscriptionInvoice.tenant_id == tenant_id,
                SubscriptionInvoice.period_start == inv.period_start,
                SubscriptionInvoice.status != "cancelada",
                SubscriptionInvoice.id != inv.id,
            )
            .with_for_update()
        )
        if other is not None and other.status == "paga":
            _divergent(
                db,
                tenant_id,
                inv,
                f"Pagamento de R$ {amount} confirmado em {today} para fatura cancelada, mas o período "
                "já estava "
                "pago por outra fatura — valor a devolver.",
                amount,
                event_id,
            )
            return "pago_em_duplicidade"
        if other is not None:  # a reativação gerou outra fatura do mesmo período: ela deixa de valer
            _cancel_invoice(other)
            db.flush()
    inv.status = "paga"
    inv.paid_at = datetime.now(UTC)
    inv.paid_amount = amount
    inv.divergence = None
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=None,
        action="subscription_invoice.paid",
        entity="subscription_invoice",
        entity_id=inv.id,
        data={"amount": str(amount), "event_id": event_id, "after_cancel": revived},
    )
    sync_invoices(db, tenant_id)
    return "pago"


def amount_due(db: Session, tenant_id: uuid.UUID) -> Decimal:
    return db.scalar(
        select(func.coalesce(func.sum(SubscriptionInvoice.amount), 0)).where(
            SubscriptionInvoice.tenant_id == tenant_id, SubscriptionInvoice.status == "aberta"
        )
    ) or Decimal("0")
