"""Fase 4a: cobranças Pix/boleto via adaptador de pagamentos e webhooks do PSP."""

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.core.clock import business_today
from app.core.config import get_settings
from app.core.db import DB_DEPENDS, set_tenant
from app.models import Charge, Customer, Installment, SalesContract, Tenant, User, WebhookEvent
from app.services import charges as svc
from app.services import psp
from app.services.psp.base import InvalidSignatureError

router = APIRouter(tags=["cobranças"])
log = logging.getLogger("imobil.webhooks")

MAX_WEBHOOK_BYTES = 64 * 1024
Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
ChargeStatus = Literal["pendente", "paga", "cancelada", "expirada", "divergente", "resolvida"]


# ─────────────────────────── DTOs ───────────────────────────


class ProviderOut(BaseModel):
    name: str
    label: str
    sandbox: bool


class ChargeIn(BaseModel):
    method: Literal["pix", "boleto"] = "pix"
    due_date: date | None = None  # padrão: vencimento da parcela (ou hoje, se já venceu)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SimulateIn(BaseModel):
    event: Literal["paid", "expired"] = "paid"
    amount: Money | None = None  # padrão: valor da cobrança (ou com encargos, se pago após o vencimento)
    paid_on: date | None = None


class ChargeOut(BaseModel):
    id: uuid.UUID
    installment_id: uuid.UUID
    installment_number: int
    contract_id: uuid.UUID
    contract_number: str
    customer_id: uuid.UUID
    customer_name: str
    provider: str
    provider_label: str
    method: str
    external_id: str
    amount: Decimal
    due_date: date
    status: str
    pix_code: str | None
    boleto_line: str | None
    paid_amount: Decimal | None
    paid_on: date | None
    payment_id: uuid.UUID | None
    status_reason: str | None
    created_by_name: str | None
    created_at: datetime
    updated_at: datetime


class WebhookEventOut(BaseModel):
    id: uuid.UUID
    event_id: str
    event_type: str
    received_at: datetime
    result: str
    detail: str | None


class ChargeDetailOut(ChargeOut):
    events: list[WebhookEventOut]


class SimulateOut(BaseModel):
    result: str
    detail: str | None
    charge: ChargeDetailOut


class ChargesSummaryOut(BaseModel):
    pending_count: int
    pending_total: Decimal
    divergent_count: int
    divergent_total: Decimal
    paid_month_count: int
    paid_month_total: Decimal


# ─────────────────────────── helpers ───────────────────────────


def _label(provider: str) -> str:
    p = psp.get_provider(provider)
    return p.label if p else provider


def _query(ctx: AuthContext) -> Any:
    return (
        select(Charge, Installment.number, SalesContract.number, Customer.name, User.full_name)
        .join(Installment, Installment.id == Charge.installment_id)
        .join(SalesContract, SalesContract.id == Charge.contract_id)
        .join(Customer, Customer.id == Charge.customer_id)
        .outerjoin(User, User.id == Charge.created_by)
        .where(Charge.tenant_id == ctx.tenant_id)
    )


def _out(row: Any) -> ChargeOut:
    c, inst_number, contract_number, customer_name, author = row
    return ChargeOut(
        id=c.id,
        installment_id=c.installment_id,
        installment_number=inst_number,
        contract_id=c.contract_id,
        contract_number=contract_number,
        customer_id=c.customer_id,
        customer_name=customer_name,
        provider=c.provider,
        provider_label=_label(c.provider),
        method=c.method,
        external_id=c.external_id,
        amount=c.amount,
        due_date=c.due_date,
        status=c.status,
        pix_code=c.pix_code,
        boleto_line=c.boleto_line,
        paid_amount=c.paid_amount,
        paid_on=c.paid_on,
        payment_id=c.payment_id,
        status_reason=c.status_reason,
        created_by_name=author,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def _detail(ctx: AuthContext, charge_id: uuid.UUID) -> ChargeDetailOut:
    ctx.db.expire_all()  # updated_at e afins vêm do banco
    row = ctx.db.execute(_query(ctx).where(Charge.id == charge_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada")
    events = ctx.db.scalars(
        select(WebhookEvent)
        .where(WebhookEvent.charge_id == charge_id, WebhookEvent.tenant_id == ctx.tenant_id)
        .order_by(WebhookEvent.received_at.desc())
    ).all()
    return ChargeDetailOut(
        **_out(row).model_dump(),
        events=[
            WebhookEventOut(
                id=e.id,
                event_id=e.event_id,
                event_type=e.event_type,
                received_at=e.received_at,
                result=e.result,
                detail=e.detail,
            )
            for e in events
        ],
    )


def _business(e: svc.ChargeError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(e, svc.ChargeNotFoundError) else 409, detail=str(e))


# ─────────────────────────── rotas autenticadas ───────────────────────────


@router.get("/payment-providers", response_model=list[ProviderOut])
def list_providers(ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))) -> list[ProviderOut]:
    return [ProviderOut(name=p.name, label=p.label, sandbox=p.name == "sandbox") for p in psp.available()]


@router.get("/charges", response_model=list[ChargeOut])
def list_charges(
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
    status_: ChargeStatus | None = Query(default=None, alias="status"),
    installment_id: uuid.UUID | None = None,
    contract_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=300, ge=1, le=1000),
) -> list[ChargeOut]:
    stmt = _query(ctx)
    if status_:
        stmt = stmt.where(Charge.status == status_)
    if installment_id:
        stmt = stmt.where(Charge.installment_id == installment_id)
    if contract_id:
        stmt = stmt.where(Charge.contract_id == contract_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Customer.name).like(like),
                SalesContract.number.like(f"%{q}%"),
                Charge.external_id == q,
            )
        )
    rows = ctx.db.execute(stmt.order_by(Charge.created_at.desc()).limit(limit)).all()
    return [_out(r) for r in rows]


@router.get("/charges/summary", response_model=ChargesSummaryOut)
def charges_summary(ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))) -> ChargesSummaryOut:
    zero = Decimal("0")
    month_start = business_today().replace(day=1)
    pending = Charge.status == "pendente"
    divergent = Charge.status == "divergente"
    paid_month = (Charge.status == "paga") & (Charge.paid_on >= month_start)
    row = ctx.db.execute(
        select(
            func.count().filter(pending),
            func.coalesce(func.sum(Charge.amount).filter(pending), zero),
            func.count().filter(divergent),
            func.coalesce(func.sum(func.coalesce(Charge.paid_amount, zero)).filter(divergent), zero),
            func.count().filter(paid_month),
            func.coalesce(func.sum(func.coalesce(Charge.paid_amount, zero)).filter(paid_month), zero),
        ).where(Charge.tenant_id == ctx.tenant_id)
    ).one()
    return ChargesSummaryOut(
        pending_count=row[0],
        pending_total=row[1],
        divergent_count=row[2],
        divergent_total=row[3],
        paid_month_count=row[4],
        paid_month_total=row[5],
    )


@router.get("/charges/{charge_id}", response_model=ChargeDetailOut)
def get_charge(
    charge_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> ChargeDetailOut:
    return _detail(ctx, charge_id)


@router.post("/installments/{installment_id}/charges", response_model=ChargeDetailOut, status_code=201)
def issue_charge(
    installment_id: uuid.UUID, body: ChargeIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> ChargeDetailOut:
    try:
        c = svc.issue_charge(
            ctx.db,
            tenant_id=ctx.tenant_id,
            installment_id=installment_id,
            method=body.method,
            due_date=body.due_date,
            actor=ctx.user.id,
        )
    except svc.ChargeError as e:
        raise _business(e) from None
    return _detail(ctx, c.id)


@router.post("/charges/{charge_id}/cancel", response_model=ChargeDetailOut)
def cancel_charge(
    charge_id: uuid.UUID, body: ReasonIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> ChargeDetailOut:
    try:
        svc.cancel_charge(
            ctx.db, tenant_id=ctx.tenant_id, charge_id=charge_id, reason=body.reason, actor=ctx.user.id
        )
    except svc.ChargeError as e:
        raise _business(e) from None
    return _detail(ctx, charge_id)


@router.post("/charges/{charge_id}/resolve", response_model=ChargeDetailOut)
def resolve_charge(
    charge_id: uuid.UUID, body: ReasonIn, ctx: AuthContext = Depends(require(Perm.FINANCE_REVERSE))
) -> ChargeDetailOut:
    try:
        svc.resolve_charge(
            ctx.db, tenant_id=ctx.tenant_id, charge_id=charge_id, reason=body.reason, actor=ctx.user.id
        )
    except svc.ChargeError as e:
        raise _business(e) from None
    return _detail(ctx, charge_id)


@router.post("/charges/{charge_id}/sandbox/simulate", response_model=SimulateOut)
def simulate_charge(
    charge_id: uuid.UUID, body: SimulateIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> SimulateOut:
    """Sandbox: o "PSP" envia um webhook assinado (pagamento ou expiração) para esta empresa."""
    try:
        out = svc.simulate(
            ctx.db,
            tenant_id=ctx.tenant_id,
            charge_id=charge_id,
            event=body.event,
            amount=body.amount,
            paid_on=body.paid_on,
        )
    except svc.ChargeError as e:
        raise _business(e) from None
    return SimulateOut(result=out.result, detail=out.detail, charge=_detail(ctx, charge_id))


# ─────────────────────────── webhook público (assinado) ───────────────────────────


async def _raw_body(request: Request) -> bytes:
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Corpo muito grande")
    return body


@router.post("/webhooks/payments/{provider}/{tenant_id}", include_in_schema=not get_settings().is_production)
def payment_webhook(
    provider: str,
    tenant_id: uuid.UUID,
    request: Request,
    body: bytes = Depends(_raw_body),
    db: Session = DB_DEPENDS,
) -> dict[str, str]:
    """Notificação do PSP. Sem token de usuário: a autenticação é a assinatura do provedor.

    A empresa vem da URL configurada no PSP; a cobrança é procurada SÓ dentro dela (RLS). Resposta
    2xx = "recebido, não reenvie" — inclusive para duplicados e eventos ignorados.
    """
    p = psp.get_provider(provider)
    if p is None:
        raise HTTPException(status_code=404, detail="Provedor desconhecido")
    try:
        p.verify_webhook(request.headers, body)
    except InvalidSignatureError as e:
        log.warning("webhook %s rejeitado: %s", provider, e)
        raise HTTPException(status_code=401, detail="Assinatura inválida") from None
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(status_code=404, detail="Destino desconhecido")
    set_tenant(db, tenant_id)
    try:
        out = svc.process_webhook(db, tenant_id=tenant_id, provider=p, body=body)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Evento malformado") from None
    return {"status": out.result}
