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
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
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
from app.services import audit, psp
from app.services.finance import add_months
from app.services.psp.base import EVENT_PAID, ChargeRequest, PaymentProvider, ProviderError

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
    """LimitError se a inclusão passar do limite do plano. Sem assinatura: sem limite."""
    sub = get_subscription(db, tenant_id)
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
        inv.status = "cancelada"
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
    provider = psp.default_provider()
    if provider is None:
        raise SubscriptionError("Nenhum provedor de pagamento habilitado neste ambiente")
    tenant = db.get(Tenant, tenant_id)
    assert tenant is not None
    due = max(inv.due_date, business_today())
    try:
        issued = provider.create_charge(
            ChargeRequest(
                reference=str(inv.id),
                method="boleto" if method == "boleto" else "pix",
                amount=inv.amount,
                due_date=due,
                payer_name=tenant.name,
                payer_document=None,
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


def process_event(db: Session, *, tenant_id: uuid.UUID, provider: PaymentProvider, body: bytes) -> str:
    """Webhook JÁ AUTENTICADO do PSP da plataforma. Idempotente por (empresa, provedor, evento)."""
    ev = provider.parse_webhook(body)
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
    if inv is None:
        result = "ignorado"
    elif ev.type != EVENT_PAID:
        result = "ignorado"
    elif inv.status == "paga":
        result = "ja_paga"
    elif ev.amount is None or ev.amount < inv.amount:
        result = "valor_divergente"
    else:
        inv.status = "paga"
        inv.paid_at = datetime.now(UTC)
        inv.paid_amount = ev.amount
        result = "pago"
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
        return "duplicado"
    if result == "pago" and inv is not None:
        audit.record(
            db,
            tenant_id=tenant_id,
            actor_user_id=None,
            action="subscription_invoice.paid",
            entity="subscription_invoice",
            entity_id=inv.id,
            data={"amount": str(ev.amount)},
        )
        sync_invoices(db, tenant_id)
    return result


def amount_due(db: Session, tenant_id: uuid.UUID) -> Decimal:
    return db.scalar(
        select(func.coalesce(func.sum(SubscriptionInvoice.amount), 0)).where(
            SubscriptionInvoice.tenant_id == tenant_id, SubscriptionInvoice.status == "aberta"
        )
    ) or Decimal("0")
