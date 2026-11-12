"""Fase 5b: portal do comprador.

Duas partes:
- `/portal/*`: usado pelo COMPRADOR, com token próprio (aud "portal-comprador" + id do comprador). Tudo é
  filtrado pelo comprador do token; registro de outro comprador responde 404. DTOs com lista fechada de
  campos: nada interno (anotações, autor do lançamento, corretor, comissão, auditoria).
- `/customers/{id}/portal*`: usado pela EQUIPE para convidar, ver a situação e bloquear o acesso.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.auth.scope import check_customer, scope_for
from app.core.db import DB_DEPENDS, set_tenant
from app.core.security import decode_portal_token
from app.models import (
    Block,
    Charge,
    ContractVersion,
    Customer,
    Development,
    Installment,
    Lot,
    Payment,
    PaymentReversal,
    PortalAccount,
    SalesContract,
    Tenant,
)
from app.services import audit
from app.services import charges as charge_svc
from app.services import portal as svc
from app.services.receivables import quote_installment, today

router = APIRouter(tags=["portal do comprador"])
_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado", headers={"WWW-Authenticate": "Bearer"}
)
VISIBLE = ("assinado", "distratado")  # rascunho/cancelado não aparecem para o comprador


# ─────────────────────────── sessão do comprador ───────────────────────────


@dataclass(frozen=True)
class PortalContext:
    db: Session
    tenant: Tenant
    account: PortalAccount
    customer: Customer

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id


def get_portal(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = DB_DEPENDS
) -> PortalContext:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _UNAUTHORIZED
    try:
        claims = svc_claims(creds.credentials)
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _UNAUTHORIZED from None
    account_id, tenant_id, customer_id = claims
    set_tenant(db, tenant_id)
    tenant = db.get(Tenant, tenant_id)
    acc = db.scalar(
        select(PortalAccount).where(
            PortalAccount.id == account_id,
            PortalAccount.tenant_id == tenant_id,
            PortalAccount.customer_id == customer_id,
            PortalAccount.is_active.is_(True),
        )
    )
    customer = db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id))
    if tenant is None or not tenant.is_active or acc is None or acc.activated_at is None or customer is None:
        raise _UNAUTHORIZED
    return PortalContext(db=db, tenant=tenant, account=acc, customer=customer)


def svc_claims(token: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    c = decode_portal_token(token)
    return uuid.UUID(c["sub"]), uuid.UUID(c["tid"]), uuid.UUID(c["cid"])


# ─────────────────────────── autenticação ───────────────────────────


class PortalLoginIn(BaseModel):
    tenant_slug: str = Field(min_length=2, max_length=60)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ActivateIn(BaseModel):
    tenant_slug: str = Field(min_length=2, max_length=60)
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=1, max_length=256)


class PortalTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - tipo do token, não segredo
    expires_in: int


def _token_out(token: str) -> PortalTokenOut:
    from app.core.config import get_settings

    return PortalTokenOut(access_token=token, expires_in=get_settings().portal_token_minutes * 60)


@router.post("/portal/auth/login", response_model=PortalTokenOut)
def portal_login(body: PortalLoginIn, db: Session = DB_DEPENDS) -> PortalTokenOut:
    tenant = svc.active_tenant(db, body.tenant_slug)
    if tenant is not None:
        set_tenant(db, tenant.id)
    try:
        token = svc.login(
            db, tenant_slug=body.tenant_slug, email=body.email, password=body.password, tenant=tenant
        )
    except svc.PortalError as e:
        raise HTTPException(status_code=429, detail=str(e)) from None
    if token is None:
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos")
    return _token_out(token)


@router.post("/portal/auth/activate", response_model=PortalTokenOut)
def portal_activate(body: ActivateIn, db: Session = DB_DEPENDS) -> PortalTokenOut:
    tenant = svc.active_tenant(db, body.tenant_slug)
    if tenant is None:
        raise HTTPException(status_code=409, detail="Convite inválido ou expirado. Peça um novo à empresa.")
    set_tenant(db, tenant.id)
    try:
        token = svc.activate(db, tenant=tenant, token=body.token, password=body.password)
    except svc.PortalError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _token_out(token)


# ─────────────────────────── dados do comprador ───────────────────────────


class PortalMeOut(BaseModel):
    customer_name: str
    email: str
    company_name: str
    company_slug: str


class NextDue(BaseModel):
    number: int
    due_date: date
    remaining: Decimal


class PortalContractOut(BaseModel):
    id: uuid.UUID
    number: str
    status: str  # assinado | distratado
    signed_at: datetime | None
    development_name: str
    block_code: str
    lot_number: str
    area_m2: Decimal
    total_price: Decimal
    paid_count: int
    open_count: int
    overdue_count: int
    open_balance: Decimal  # soma do em aberto das parcelas (sem encargos)
    next_due: NextDue | None


class PortalChargeOut(BaseModel):
    id: uuid.UUID
    method: str
    amount: Decimal
    due_date: date
    pix_code: str | None
    boleto_line: str | None


class PortalInstallmentOut(BaseModel):
    id: uuid.UUID
    number: int
    label: str  # "Entrada", "Parcela 3/120" ou "Parcela 2/24 (renegociação)"
    due_date: date
    amount: Decimal
    remaining: Decimal
    status: str  # aberta | paga | renegociada | cancelada
    overdue: bool
    days_late: int
    paid_at: date | None
    pending_charge: PortalChargeOut | None


class PortalPaymentOut(BaseModel):
    received_on: date
    installment_number: int
    installment_label: str
    amount: Decimal
    principal: Decimal
    late_charges: Decimal
    method: str
    reversed: bool


class PortalTermsOut(BaseModel):
    total_price: Decimal
    down_payment: Decimal
    installments_count: int
    amortization: str
    interest_monthly_pct: Decimal
    adjustment_index: str
    late_fee_pct: Decimal
    late_interest_monthly_pct: Decimal


class PortalContractDetailOut(PortalContractOut):
    terms: PortalTermsOut
    renegotiated: bool  # parcelas atuais vêm de renegociação (as condições originais ficam em `terms`)
    installments: list[PortalInstallmentOut]
    payments: list[PortalPaymentOut]


def _contracts(pc: PortalContext, contract_id: uuid.UUID | None = None) -> list[PortalContractOut]:
    on = today()
    stmt = (
        select(
            SalesContract, ContractVersion.total_price, Development.name, Block.code, Lot.number, Lot.area_m2
        )
        .join(
            ContractVersion,
            (ContractVersion.contract_id == SalesContract.id)
            & (ContractVersion.version == SalesContract.current_version),
        )
        .join(Lot, Lot.id == SalesContract.lot_id)
        .join(Block, Block.id == Lot.block_id)
        .join(Development, Development.id == Lot.development_id)
        .where(
            SalesContract.tenant_id == pc.tenant_id,
            SalesContract.customer_id == pc.customer.id,
            SalesContract.status.in_(VISIBLE),
        )
        .order_by(SalesContract.signed_at.desc())
    )
    if contract_id is not None:
        stmt = stmt.where(SalesContract.id == contract_id)
    out: list[PortalContractOut] = []
    for c, total, dev, block, lot, area in pc.db.execute(stmt).all():
        insts = pc.db.scalars(
            select(Installment)
            .where(Installment.contract_id == c.id)
            .order_by(Installment.due_date, Installment.number)
        ).all()
        open_ = [i for i in insts if i.status == "aberta"]
        nxt = open_[0] if open_ else None
        out.append(
            PortalContractOut(
                id=c.id,
                number=c.number,
                status=c.status,
                signed_at=c.signed_at,
                development_name=dev,
                block_code=block,
                lot_number=lot,
                area_m2=area,
                total_price=total,
                paid_count=sum(1 for i in insts if i.status == "paga"),
                open_count=len(open_),
                overdue_count=sum(1 for i in open_ if i.due_date < on),
                open_balance=sum((i.amount - i.paid_principal for i in open_), Decimal("0.00")),
                next_due=NextDue(
                    number=nxt.number, due_date=nxt.due_date, remaining=nxt.amount - nxt.paid_principal
                )
                if nxt
                else None,
            )
        )
    return out


def _labels(insts: "list[Installment] | Sequence[Installment]") -> dict[uuid.UUID, str]:
    """Numeração que o comprador entende: a do plano original ou a da renegociação (1/n)."""
    groups: dict[uuid.UUID | None, list[Installment]] = {}
    for i in sorted(insts, key=lambda x: x.number):
        if i.number > 0 or i.renegotiation_id is not None:
            groups.setdefault(i.renegotiation_id, []).append(i)
    out: dict[uuid.UUID, str] = {
        i.id: "Entrada" for i in insts if i.number == 0 and i.renegotiation_id is None
    }
    for reneg, items in groups.items():
        for k, i in enumerate(items, start=1):
            out[i.id] = f"Parcela {k}/{len(items)}" + (" (renegociação)" if reneg else "")
    return out


def _own_installment(pc: PortalContext, installment_id: uuid.UUID) -> Installment:
    inst = pc.db.scalar(
        select(Installment)
        .join(SalesContract, SalesContract.id == Installment.contract_id)
        .where(
            Installment.id == installment_id,
            Installment.tenant_id == pc.tenant_id,
            SalesContract.customer_id == pc.customer.id,
            SalesContract.status.in_(VISIBLE),
        )
    )
    if inst is None:
        raise HTTPException(status_code=404, detail="Parcela não encontrada")
    return inst


def _charge_out(ch: Charge | None) -> PortalChargeOut | None:
    if ch is None:
        return None
    return PortalChargeOut(
        id=ch.id,
        method=ch.method,
        amount=ch.amount,
        due_date=ch.due_date,
        pix_code=ch.pix_code,
        boleto_line=ch.boleto_line,
    )


@router.get("/portal/me", response_model=PortalMeOut)
def portal_me(pc: PortalContext = Depends(get_portal)) -> PortalMeOut:
    return PortalMeOut(
        customer_name=pc.customer.name,
        email=pc.account.email,
        company_name=pc.tenant.name,
        company_slug=pc.tenant.slug,
    )


@router.get("/portal/contracts", response_model=list[PortalContractOut])
def portal_contracts(pc: PortalContext = Depends(get_portal)) -> list[PortalContractOut]:
    return _contracts(pc)


@router.get("/portal/contracts/{contract_id}", response_model=PortalContractDetailOut)
def portal_contract(
    contract_id: uuid.UUID, pc: PortalContext = Depends(get_portal)
) -> PortalContractDetailOut:
    found = _contracts(pc, contract_id)
    if not found:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    c = pc.db.scalar(select(SalesContract).where(SalesContract.id == contract_id))
    assert c is not None
    v = pc.db.scalar(
        select(ContractVersion).where(
            ContractVersion.contract_id == c.id, ContractVersion.version == c.current_version
        )
    )
    assert v is not None
    on = today()
    insts = pc.db.scalars(
        select(Installment).where(Installment.contract_id == c.id).order_by(Installment.number)
    ).all()
    pending = {
        ch.installment_id: ch
        for ch in pc.db.scalars(
            select(Charge).where(Charge.contract_id == c.id, Charge.status == "pendente")
        ).all()
    }
    labels = _labels(insts)
    pays = pc.db.execute(
        select(Payment, PaymentReversal.id)
        .outerjoin(PaymentReversal, PaymentReversal.payment_id == Payment.id)
        .where(Payment.contract_id == c.id)
        .order_by(Payment.received_on.desc(), Payment.created_at.desc())
    ).all()
    return PortalContractDetailOut(
        **found[0].model_dump(),
        renegotiated=any(i.renegotiation_id is not None and i.status != "renegociada" for i in insts),
        terms=PortalTermsOut(
            total_price=v.total_price,
            down_payment=v.down_payment,
            installments_count=v.installments_count,
            amortization=v.amortization,
            interest_monthly_pct=v.interest_monthly_pct,
            adjustment_index=v.adjustment_index,
            late_fee_pct=v.late_fee_pct,
            late_interest_monthly_pct=v.late_interest_monthly_pct,
        ),
        installments=[
            PortalInstallmentOut(
                id=i.id,
                number=i.number,
                label=labels[i.id],
                due_date=i.due_date,
                amount=i.amount,
                remaining=i.amount - i.paid_principal,
                status=i.status,
                overdue=i.status == "aberta" and i.due_date < on,
                days_late=(on - i.due_date).days if i.status == "aberta" and i.due_date < on else 0,
                paid_at=i.paid_at,
                pending_charge=_charge_out(pending.get(i.id)),
            )
            for i in insts
        ],
        payments=[
            PortalPaymentOut(
                received_on=p.received_on,
                installment_number=next((i.number for i in insts if i.id == p.installment_id), 0),
                installment_label=labels.get(p.installment_id, ""),
                amount=p.amount,
                principal=p.principal,
                late_charges=p.late_fee + p.late_interest,
                method=p.method,
                reversed=rev is not None,
            )
            for p, rev in pays
        ],
    )


class PortalQuoteOut(BaseModel):
    on: date
    remaining: Decimal
    late_fee: Decimal
    late_interest: Decimal
    total: Decimal
    days_late: int


@router.get("/portal/installments/{installment_id}/quote", response_model=PortalQuoteOut)
def portal_quote(installment_id: uuid.UUID, pc: PortalContext = Depends(get_portal)) -> PortalQuoteOut:
    inst = _own_installment(pc, installment_id)
    on = today()
    q = quote_installment(pc.db, inst, on)
    return PortalQuoteOut(
        on=on,
        remaining=q.principal,
        late_fee=q.late_fee,
        late_interest=q.late_interest,
        total=q.total,
        days_late=q.days_late,
    )


class PortalChargeIn(BaseModel):
    method: Literal["pix", "boleto"] = "pix"


@router.post("/portal/installments/{installment_id}/charges", response_model=PortalChargeOut)
def portal_issue_charge(
    installment_id: uuid.UUID, body: PortalChargeIn, pc: PortalContext = Depends(get_portal)
) -> PortalChargeOut:
    """2ª via: se já há cobrança pendente da parcela, devolve a mesma (o comprador não cancela cobranças)."""
    inst = _own_installment(pc, installment_id)
    pending = pc.db.scalar(
        select(Charge).where(Charge.installment_id == inst.id, Charge.status == "pendente")
    )
    if pending is not None:
        return _charge_out(pending)  # type: ignore[return-value]
    try:
        ch = charge_svc.issue_charge(
            pc.db,
            tenant_id=pc.tenant_id,
            installment_id=inst.id,
            method=body.method,
            due_date=None,
            actor=None,
            origin="portal",
        )
    except charge_svc.ChargeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _charge_out(ch)  # type: ignore[return-value]


class StatementLine(BaseModel):
    received_on: date
    contract_number: str
    installment_number: int
    amount: Decimal
    principal: Decimal
    late_charges: Decimal


class PortalStatementOut(BaseModel):
    year: int
    company_name: str
    customer_name: str
    customer_document: str | None
    lines: list[StatementLine]
    total: Decimal
    total_principal: Decimal
    total_late_charges: Decimal


@router.get("/portal/statement", response_model=PortalStatementOut)
def portal_statement(
    pc: PortalContext = Depends(get_portal), year: int | None = Query(default=None, ge=2000, le=2100)
) -> PortalStatementOut:
    """Informe de pagamentos do ano (para a declaração de IR). Estornados não entram."""
    y = year or today().year - 1
    rows = pc.db.execute(
        select(Payment, SalesContract.number, Installment.number)
        .join(SalesContract, SalesContract.id == Payment.contract_id)
        .join(Installment, Installment.id == Payment.installment_id)
        .outerjoin(PaymentReversal, PaymentReversal.payment_id == Payment.id)
        .where(
            Payment.tenant_id == pc.tenant_id,
            SalesContract.customer_id == pc.customer.id,
            PaymentReversal.id.is_(None),
            func.extract("year", Payment.received_on) == y,
        )
        .order_by(Payment.received_on)
    ).all()
    lines = [
        StatementLine(
            received_on=p.received_on,
            contract_number=num,
            installment_number=inum,
            amount=p.amount,
            principal=p.principal,
            late_charges=p.late_fee + p.late_interest,
        )
        for p, num, inum in rows
    ]
    zero = Decimal("0.00")
    return PortalStatementOut(
        year=y,
        company_name=pc.tenant.name,
        customer_name=pc.customer.name,
        customer_document=pc.customer.document,
        lines=lines,
        total=sum((x.amount for x in lines), zero),
        total_principal=sum((x.principal for x in lines), zero),
        total_late_charges=sum((x.late_charges for x in lines), zero),
    )


# ─────────────────────────── equipe: convite e situação ───────────────────────────


class InviteIn(BaseModel):
    email: str | None = Field(default=None, max_length=254)


class PortalAccessOut(BaseModel):
    exists: bool
    email: str | None = None
    is_active: bool = False
    activated: bool = False
    invited_at: datetime | None = None
    activated_at: datetime | None = None
    last_login_at: datetime | None = None
    invite_expires_at: datetime | None = None


class InviteOut(PortalAccessOut):
    invite_path: str  # mostrado UMA vez; o servidor guarda só o hash


class AccessUpdateIn(BaseModel):
    is_active: bool
    reason: str = Field(min_length=3, max_length=500)


def _customer(ctx: AuthContext, customer_id: uuid.UUID) -> Customer:
    check_customer(ctx, scope_for(ctx), customer_id)
    c = ctx.db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == ctx.tenant_id))
    assert c is not None
    return c


def _access(ctx: AuthContext, customer_id: uuid.UUID) -> PortalAccessOut:
    acc = ctx.db.scalar(
        select(PortalAccount).where(
            PortalAccount.tenant_id == ctx.tenant_id, PortalAccount.customer_id == customer_id
        )
    )
    if acc is None:
        return PortalAccessOut(exists=False)
    return PortalAccessOut(
        exists=True,
        email=acc.email,
        is_active=acc.is_active,
        activated=acc.activated_at is not None,
        invited_at=acc.invited_at,
        activated_at=acc.activated_at,
        last_login_at=acc.last_login_at,
        invite_expires_at=svc.pending_invite_expires(ctx.db, acc.id),
    )


@router.get("/customers/{customer_id}/portal", response_model=PortalAccessOut)
def get_portal_access(
    customer_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.CUSTOMERS_VIEW))
) -> PortalAccessOut:
    _customer(ctx, customer_id)
    return _access(ctx, customer_id)


@router.post("/customers/{customer_id}/portal-invite", response_model=InviteOut)
def invite_to_portal(
    customer_id: uuid.UUID, body: InviteIn, ctx: AuthContext = Depends(require(Perm.CUSTOMERS_MANAGE))
) -> InviteOut:
    c = _customer(ctx, customer_id)
    try:
        inv = svc.invite(ctx.db, tenant_id=ctx.tenant_id, customer=c, email=body.email, actor=ctx.user.id)
    except svc.PortalError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return InviteOut(
        **_access(ctx, customer_id).model_dump(),
        invite_path=f"/portal/{ctx.tenant.slug}/ativar?token={inv.token}",
    )


@router.patch("/customers/{customer_id}/portal", response_model=PortalAccessOut)
def update_portal_access(
    customer_id: uuid.UUID, body: AccessUpdateIn, ctx: AuthContext = Depends(require(Perm.CUSTOMERS_MANAGE))
) -> PortalAccessOut:
    _customer(ctx, customer_id)
    acc = ctx.db.scalar(
        select(PortalAccount)
        .where(PortalAccount.tenant_id == ctx.tenant_id, PortalAccount.customer_id == customer_id)
        .with_for_update()
    )
    if acc is None:
        raise HTTPException(status_code=404, detail="Comprador ainda não tem acesso ao portal")
    acc.is_active = body.is_active
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="portal.enable" if body.is_active else "portal.disable",
        entity="customer",
        entity_id=customer_id,
        reason=body.reason,
    )
    return _access(ctx, customer_id)
