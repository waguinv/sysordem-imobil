"""Fase 5c: planos SaaS, cadastro self-service, assinatura, faturas, webhook e primeiros passos."""

import logging
import re
import threading
import time
import uuid
from collections import deque
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.core.client_ip import client_ip
from app.core.config import get_settings
from app.core.db import DB_DEPENDS, set_tenant
from app.core.security import create_access_token, hash_password
from app.models import (
    Block,
    ContractPolicy,
    Customer,
    Development,
    Lot,
    Membership,
    Plan,
    SiteSettings,
    SubscriptionInvoice,
    Tenant,
    User,
)
from app.services import audit, ip_throttle
from app.services import subscription as svc
from app.services.finance import money
from app.services.psp.base import EVENT_PAID, InvalidSignatureError, ProviderError
from app.services.psp.sandbox import SandboxProvider

log = logging.getLogger("imobil.billing")
router = APIRouter(tags=["assinatura"])

MAX_WEBHOOK_BYTES = 64 * 1024
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
RESERVED_SLUGS = {
    "app",
    "api",
    "admin",
    "portal",
    "s",
    "www",
    "entrar",
    "cadastro",
    "sysordem",
    "suporte",
    "static",
}


# ─────────────────────────── planos (público) ───────────────────────────


class PlanOut(BaseModel):
    code: str
    name: str
    monthly_price: Decimal
    max_developments: int | None
    max_lots: int | None
    max_users: int | None


def _plans(db: Session) -> list[PlanOut]:
    return [
        PlanOut(
            code=p.code,
            name=p.name,
            monthly_price=p.monthly_price,
            max_developments=p.max_developments,
            max_lots=p.max_lots,
            max_users=p.max_users,
        )
        for p in db.scalars(select(Plan).where(Plan.is_public.is_(True)).order_by(Plan.sort_order)).all()
    ]


@router.get("/public/plans", response_model=list[PlanOut])
def public_plans(db: Session = DB_DEPENDS) -> list[PlanOut]:
    return _plans(db)


# ─────────────────────────── cadastro self-service (público) ───────────────────────────

_signups: deque[float] = deque()
_signup_lock = threading.Lock()
SIGNUP_MAX_PER_HOUR = 20  # por processo (mover para Redis com várias réplicas)


def _signup_allowed() -> bool:
    now = time.monotonic()
    with _signup_lock:
        while _signups and now - _signups[0] > 3600:
            _signups.popleft()
        if len(_signups) >= SIGNUP_MAX_PER_HOUR:
            return False
        _signups.append(now)
        return True


def reset_signup_limit() -> None:
    with _signup_lock:
        _signups.clear()


class SignupIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=160)
    slug: str = Field(min_length=3, max_length=40)
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    plan_code: str = Field(default="profissional", max_length=30)
    accept_terms: bool
    website: str | None = Field(default=None, max_length=200)  # campo-isca (robôs preenchem)


class SignupOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - tipo do token, não segredo
    expires_in: int
    tenant_slug: str
    trial_ends_at: date


@router.post("/signup", response_model=SignupOut, status_code=201)
def signup(body: SignupIn, db: Session = DB_DEPENDS, ip: str | None = Depends(client_ip)) -> SignupOut:
    """Cria empresa + proprietário + assinatura em teste. Sem token: é a porta de entrada do SaaS."""
    if not ip_throttle.take("cadastro", ip):
        raise HTTPException(
            status_code=429, detail="Muitos cadastros a partir deste endereço. Tente de novo mais tarde."
        )
    if body.website:
        raise HTTPException(status_code=400, detail="Não foi possível concluir o cadastro")
    if not body.accept_terms:
        raise HTTPException(status_code=422, detail="Aceite os termos de uso e a política de privacidade")
    slug = body.slug.strip().lower()
    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        raise HTTPException(
            status_code=422, detail="Identificador inválido: use 3 a 40 letras minúsculas, números e hífen"
        )
    plan = db.get(Plan, body.plan_code)
    if plan is None or not plan.is_public:
        raise HTTPException(status_code=422, detail="Plano inválido")
    if not _signup_allowed():
        raise HTTPException(
            status_code=429, detail="Muitos cadastros agora. Tente de novo em alguns minutos."
        )
    if db.scalar(select(Tenant.id).where(Tenant.slug == slug)) is not None:
        raise HTTPException(status_code=409, detail="Este identificador já está em uso. Escolha outro.")
    email = body.email.lower()
    if db.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
        raise HTTPException(
            status_code=409,
            detail="Este e-mail já tem conta. Entre com ele; para outra empresa, peça o convite ao "
            "proprietário.",
        )

    tenant_id = uuid.uuid4()
    user = User(email=email, full_name=body.full_name.strip(), password_hash=hash_password(body.password))
    try:
        # cadastro simultâneo com o mesmo identificador/e-mail: o índice único barra o segundo
        with db.begin_nested():
            # Papel da aplicação só pode inserir (id, slug, name) em tenants — o resto vem dos padrões.
            db.execute(
                text("INSERT INTO tenants (id, slug, name) VALUES (:i, :s, :n)"),
                {"i": tenant_id, "s": slug, "n": body.company_name.strip()},
            )
            db.add(user)
            db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail="Identificador ou e-mail já em uso. Confira e tente de novo."
        ) from None
    set_tenant(db, tenant_id)
    m = Membership(tenant_id=tenant_id, user_id=user.id, role="owner")
    db.add(m)
    db.flush()
    try:
        sub = svc.start(db, tenant_id=tenant_id, plan_code=plan.code, actor=user.id)
    except svc.SubscriptionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=user.id,
        action="tenant.signup",
        entity="tenant",
        entity_id=tenant_id,
        data={
            "slug": slug,
            "plan": body.plan_code,
            "terms_accepted_at": datetime.now().astimezone().isoformat(),
        },
    )
    s = get_settings()
    return SignupOut(
        access_token=create_access_token(user_id=user.id, tenant_id=tenant_id, membership_id=m.id),
        expires_in=s.access_token_minutes * 60,
        tenant_slug=slug,
        trial_ends_at=sub.trial_ends_at,
    )


# ─────────────────────────── assinatura da empresa ───────────────────────────


class StatusOut(BaseModel):
    state: str
    plan_code: str | None
    plan_name: str | None
    trial_ends_at: date | None
    covered_until: date | None  # exclusivo: 1º dia NÃO coberto
    covered_through: date | None  # último dia coberto (para exibir)
    grace_until: date | None
    cancelled: bool
    can_write: bool


class UsageOut(BaseModel):
    developments: int
    lots: int
    users: int


class InvoiceOut(BaseModel):
    id: uuid.UUID
    plan_code: str
    period_start: date
    period_end: date
    due_date: date
    amount: Decimal
    status: str
    method: str | None
    pix_code: str | None
    boleto_line: str | None
    payment_url: str | None
    paid_at: datetime | None
    divergence: str | None


class BillingOut(BaseModel):
    status: StatusOut
    usage: UsageOut
    plans: list[PlanOut]
    invoices: list[InvoiceOut]
    amount_due: Decimal
    sandbox: bool


def _status(ctx: AuthContext) -> StatusOut:
    st = svc.status(ctx.db, ctx.tenant_id)
    return StatusOut(
        state=st.state,
        plan_code=st.plan.code if st.plan else None,
        plan_name=st.plan.name if st.plan else None,
        trial_ends_at=st.trial_ends_at,
        covered_until=st.covered_until,
        covered_through=st.covered_until - timedelta(days=1) if st.covered_until else None,
        grace_until=st.grace_until,
        cancelled=st.cancelled,
        can_write=st.can_write,
    )


def _invoice_out(i: SubscriptionInvoice) -> InvoiceOut:
    return InvoiceOut(
        id=i.id,
        plan_code=i.plan_code,
        period_start=i.period_start,
        period_end=i.period_end,
        due_date=i.due_date,
        amount=i.amount,
        status=i.status,
        method=i.method,
        pix_code=i.pix_code,
        boleto_line=i.boleto_line,
        payment_url=i.payment_url,
        paid_at=i.paid_at,
        divergence=i.divergence,
    )


def _billing(ctx: AuthContext) -> BillingOut:
    svc.sync_invoices(ctx.db, ctx.tenant_id)
    u = svc.usage(ctx.db, ctx.tenant_id)
    invoices = ctx.db.scalars(
        select(SubscriptionInvoice)
        .where(SubscriptionInvoice.tenant_id == ctx.tenant_id)
        .order_by(SubscriptionInvoice.period_start.desc())
    ).all()
    return BillingOut(
        status=_status(ctx),
        usage=UsageOut(developments=u.developments, lots=u.lots, users=u.users),
        plans=_plans(ctx.db),
        invoices=[_invoice_out(i) for i in invoices],
        amount_due=money(svc.amount_due(ctx.db, ctx.tenant_id)),
        sandbox=svc.billing_provider("sandbox") is not None,
    )


def _business(e: svc.SubscriptionError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(e, svc.NotFoundError) else 409, detail=str(e))


@router.get("/billing/status", response_model=StatusOut)
def billing_status(ctx: AuthContext = Depends(require(Perm.TENANT_VIEW))) -> StatusOut:
    """Situação para o aviso no topo do sistema (qualquer membro). Só leitura: a fatura é gerada ao abrir
    a assinatura, ao pagar e ao reativar."""
    return _status(ctx)


@router.get("/billing", response_model=BillingOut)
def get_billing(ctx: AuthContext = Depends(require(Perm.BILLING_VIEW))) -> BillingOut:
    return _billing(ctx)


class PlanChangeIn(BaseModel):
    plan_code: str = Field(max_length=30)
    reason: str = Field(default="mudança de plano", min_length=3, max_length=500)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PayIn(BaseModel):
    method: Literal["pix", "boleto"] = "pix"


@router.post("/billing/plan", response_model=BillingOut)
def change_plan(
    body: PlanChangeIn, ctx: AuthContext = Depends(require(Perm.BILLING_MANAGE, allow_suspended=True))
) -> BillingOut:
    try:
        svc.change_plan(
            ctx.db, tenant_id=ctx.tenant_id, plan_code=body.plan_code, actor=ctx.user.id, reason=body.reason
        )
    except svc.SubscriptionError as e:
        raise _business(e) from None
    return _billing(ctx)


@router.post("/billing/cancel", response_model=BillingOut)
def cancel_subscription(
    body: ReasonIn, ctx: AuthContext = Depends(require(Perm.BILLING_MANAGE, allow_suspended=True))
) -> BillingOut:
    try:
        svc.cancel(ctx.db, tenant_id=ctx.tenant_id, actor=ctx.user.id, reason=body.reason)
    except svc.SubscriptionError as e:
        raise _business(e) from None
    return _billing(ctx)


@router.post("/billing/reactivate", response_model=BillingOut)
def reactivate_subscription(
    ctx: AuthContext = Depends(require(Perm.BILLING_MANAGE, allow_suspended=True)),
) -> BillingOut:
    try:
        svc.reactivate(ctx.db, tenant_id=ctx.tenant_id, actor=ctx.user.id)
    except svc.SubscriptionError as e:
        raise _business(e) from None
    return _billing(ctx)


@router.post("/billing/invoices/{invoice_id}/pay", response_model=InvoiceOut)
def pay_invoice(
    invoice_id: uuid.UUID,
    body: PayIn,
    ctx: AuthContext = Depends(require(Perm.BILLING_MANAGE, allow_suspended=True)),
) -> InvoiceOut:
    try:
        inv = svc.pay(
            ctx.db, tenant_id=ctx.tenant_id, invoice_id=invoice_id, method=body.method, actor=ctx.user.id
        )
    except svc.SubscriptionError as e:
        raise _business(e) from None
    return _invoice_out(inv)


class SimulateOut(BaseModel):
    result: str
    billing: BillingOut


@router.post("/billing/invoices/{invoice_id}/sandbox/simulate", response_model=SimulateOut)
def simulate_invoice_payment(
    invoice_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.BILLING_MANAGE, allow_suspended=True))
) -> SimulateOut:
    """Sandbox: o "PSP" da plataforma envia um webhook assinado de pagamento (mesmo caminho do externo)."""
    p = svc.billing_provider("sandbox")
    if not isinstance(p, SandboxProvider):
        raise HTTPException(status_code=404, detail="Simulador indisponível neste ambiente")
    inv = ctx.db.scalar(
        select(SubscriptionInvoice).where(
            SubscriptionInvoice.id == invoice_id, SubscriptionInvoice.tenant_id == ctx.tenant_id
        )
    )
    if inv is None:
        raise HTTPException(status_code=404, detail="Fatura não encontrada")
    if inv.external_id is None or inv.provider != p.name:
        raise HTTPException(
            status_code=409, detail="Gere o Pix/boleto da fatura antes de simular o pagamento"
        )
    body, headers = p.build_event(etype=EVENT_PAID, external_id=inv.external_id, amount=inv.amount)
    p.verify_webhook(headers, body)
    result = svc.process_event(ctx.db, tenant_id=ctx.tenant_id, provider=p, body=body)
    return SimulateOut(result=result, billing=_billing(ctx))


async def _raw_body(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Corpo muito grande")
    return body


@router.post("/webhooks/billing/asaas", include_in_schema=not get_settings().is_production)
def billing_webhook_asaas(
    request: Request, body: bytes = Depends(_raw_body), db: Session = DB_DEPENDS
) -> dict[str, str]:
    """Webhook ÚNICO da conta Asaas da plataforma: a empresa vem da referência da fatura
    (`<empresa>:<fatura>`), lida só depois do token conferido e do pagamento confirmado na API."""
    p = svc.billing_provider("asaas")
    if p is None:
        raise HTTPException(status_code=404, detail="Provedor desconhecido")
    try:
        p.verify_webhook(request.headers, body)
    except InvalidSignatureError as e:
        log.warning("webhook de assinatura asaas rejeitado: %s", e)
        raise HTTPException(status_code=401, detail="Assinatura inválida") from None
    try:
        ev = p.parse_webhook(body)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Evento malformado") from None
    except ProviderError:
        raise HTTPException(status_code=503, detail="Confirmação no PSP indisponível") from None
    tenant_id = svc.tenant_from_reference(ev.reference)
    tenant = db.get(Tenant, tenant_id) if tenant_id else None
    if tenant is None or tenant_id is None:
        log.info("webhook de assinatura asaas sem empresa conhecida (%s)", ev.type)
        return {"status": "ignorado"}
    set_tenant(db, tenant_id)
    return {"status": svc.process_event(db, tenant_id=tenant_id, provider=p, event=ev)}


@router.post("/webhooks/billing/{provider}/{tenant_id}", include_in_schema=not get_settings().is_production)
def billing_webhook(
    provider: str,
    tenant_id: uuid.UUID,
    request: Request,
    body: bytes = Depends(_raw_body),
    db: Session = DB_DEPENDS,
) -> dict[str, str]:
    """Notificação do PSP da plataforma sobre faturas da assinatura. Autenticação = assinatura do PSP."""
    p = svc.billing_provider(provider)
    if p is None:
        raise HTTPException(status_code=404, detail="Provedor desconhecido")
    try:
        p.verify_webhook(request.headers, body)
    except InvalidSignatureError as e:
        log.warning("webhook de assinatura %s rejeitado: %s", provider, e)
        raise HTTPException(status_code=401, detail="Assinatura inválida") from None
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Destino desconhecido")
    set_tenant(db, tenant_id)
    try:
        result = svc.process_event(db, tenant_id=tenant_id, provider=p, body=body)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Evento malformado") from None
    return {"status": result}


# ─────────────────────────── primeiros passos ───────────────────────────


class OnboardingStep(BaseModel):
    key: str
    title: str
    done: bool
    href: str


class OnboardingOut(BaseModel):
    steps: list[OnboardingStep]
    done: int
    total: int


@router.get("/onboarding", response_model=OnboardingOut)
def onboarding(ctx: AuthContext = Depends(require(Perm.TENANT_VIEW))) -> OnboardingOut:
    db, t = ctx.db, ctx.tenant_id
    count = lambda model: db.scalar(select(func.count()).where(model.tenant_id == t)) or 0  # noqa: E731
    site = db.scalar(select(SiteSettings.published).where(SiteSettings.tenant_id == t))
    members = (
        db.scalar(select(func.count()).where(Membership.tenant_id == t, Membership.is_active.is_(True))) or 0
    )
    steps = [
        OnboardingStep(key="empreendimento", title="Cadastre o primeiro empreendimento",
                       done=count(Development) > 0, href="/app/empreendimentos/novo"),
        OnboardingStep(key="lotes", title="Crie as quadras e os lotes",
                       done=count(Block) > 0 and count(Lot) > 0, href="/app/empreendimentos"),
        OnboardingStep(key="politica", title="Revise a política de contratos (juros, multa, parcelas)",
                       done=count(ContractPolicy) > 0, href="/app/configuracoes/politica-contratos"),
        OnboardingStep(key="equipe", title="Convide a equipe", done=members > 1, href="/app/equipe"),
        OnboardingStep(key="comprador", title="Cadastre o primeiro comprador", done=count(Customer) > 0,
                       href="/app/compradores"),
        OnboardingStep(key="site", title="Publique o site da empresa", done=bool(site), href="/app/site"),
    ]  # fmt: skip
    done = sum(1 for s in steps if s.done)
    return OnboardingOut(steps=steps, done=done, total=len(steps))
