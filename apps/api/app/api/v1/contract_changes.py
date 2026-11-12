"""Fase 3c: renegociação e distrato de contratos assinados."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.models import Renegotiation, Rescission, SalesContract, User
from app.services import contract_changes as svc
from app.services.receivables import today

router = APIRouter(tags=["renegociação e distrato"])

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Pct = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]


class OpenBalanceOut(BaseModel):
    count: int
    overdue_count: int
    balance: Decimal
    late_charges: Decimal


class RenegotiateIn(BaseModel):
    installments_count: int = Field(ge=1, le=480)
    interest_monthly_pct: Annotated[Decimal, Field(ge=0, le=10, decimal_places=4)] | None = None
    amortization: Literal["price", "sac"] | None = None
    first_due_date: date
    include_late_charges: bool = True
    discount: Money = Decimal("0")
    reason: str = Field(min_length=3, max_length=500)


class RenegotiationOut(BaseModel):
    id: uuid.UUID
    old_balance: Decimal
    late_charges: Decimal
    discount: Decimal
    new_balance: Decimal
    replaced_count: int
    installments_count: int
    interest_monthly_pct: Decimal
    amortization: str
    first_due_date: date
    reason: str
    author_name: str | None
    created_at: datetime


class RescissionQuoteOut(BaseModel):
    contract_total: Decimal
    paid_total: Decimal
    retention_pct: Decimal
    retention_amount: Decimal
    refund_amount: Decimal
    open_count: int


class RescindIn(BaseModel):
    retention_pct: Pct = svc.DEFAULT_RETENTION_PCT
    refund_due_date: date | None = None  # padrão: hoje + 30 dias
    reason: str = Field(min_length=3, max_length=500)


class RescissionOut(BaseModel):
    paid_total: Decimal
    retention_pct: Decimal
    retention_amount: Decimal
    refund_amount: Decimal
    payable_id: uuid.UUID | None
    reason: str
    author_name: str | None
    created_at: datetime


def _contract(ctx: AuthContext, contract_id: uuid.UUID) -> SalesContract:
    c = ctx.db.scalar(
        select(SalesContract).where(SalesContract.id == contract_id, SalesContract.tenant_id == ctx.tenant_id)
    )
    if c is None:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    return c


def _business(e: svc.ChangeError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(e, svc.ChangeNotFoundError) else 409, detail=str(e))


def _renegotiations(ctx: AuthContext, contract_id: uuid.UUID) -> list[RenegotiationOut]:
    rows = ctx.db.execute(
        select(Renegotiation, User.full_name)
        .outerjoin(User, User.id == Renegotiation.created_by)
        .where(Renegotiation.contract_id == contract_id, Renegotiation.tenant_id == ctx.tenant_id)
        .order_by(Renegotiation.created_at.desc())
    ).all()
    return [
        RenegotiationOut(
            id=r.id,
            old_balance=r.old_balance,
            late_charges=r.late_charges,
            discount=r.discount,
            new_balance=r.new_balance,
            replaced_count=r.replaced_count,
            installments_count=r.installments_count,
            interest_monthly_pct=r.interest_monthly_pct,
            amortization=r.amortization,
            first_due_date=r.first_due_date,
            reason=r.reason,
            author_name=author,
            created_at=r.created_at,
        )
        for r, author in rows
    ]


# ─────────────────────────── renegociação ───────────────────────────


@router.get("/contracts/{contract_id}/open-balance", response_model=OpenBalanceOut)
def get_open_balance(
    contract_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> OpenBalanceOut:
    c = _contract(ctx, contract_id)
    b = svc.open_balance(ctx.db, c.id)
    return OpenBalanceOut(
        count=b.count, overdue_count=b.overdue_count, balance=b.balance, late_charges=b.late_charges
    )


@router.get("/contracts/{contract_id}/renegotiations", response_model=list[RenegotiationOut])
def list_renegotiations(
    contract_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> list[RenegotiationOut]:
    _contract(ctx, contract_id)
    return _renegotiations(ctx, contract_id)


@router.post("/contracts/{contract_id}/renegotiations", response_model=RenegotiationOut, status_code=201)
def create_renegotiation(
    contract_id: uuid.UUID, body: RenegotiateIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> RenegotiationOut:
    _contract(ctx, contract_id)
    try:
        r = svc.renegotiate(
            ctx.db,
            tenant_id=ctx.tenant_id,
            contract_id=contract_id,
            installments_count=body.installments_count,
            interest_monthly_pct=body.interest_monthly_pct,
            amortization=body.amortization,
            first_due_date=body.first_due_date,
            include_late_charges=body.include_late_charges,
            discount=body.discount,
            actor=ctx.user.id,
            reason=body.reason,
        )
    except svc.ChangeError as e:
        raise _business(e) from None
    return next(x for x in _renegotiations(ctx, contract_id) if x.id == r.id)


# ─────────────────────────── distrato ───────────────────────────


@router.get("/contracts/{contract_id}/rescission-quote", response_model=RescissionQuoteOut)
def get_rescission_quote(
    contract_id: uuid.UUID,
    retention_pct: Decimal = Query(default=svc.DEFAULT_RETENTION_PCT, ge=0, le=100),
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
) -> RescissionQuoteOut:
    c = _contract(ctx, contract_id)
    if c.status != "assinado":
        raise HTTPException(status_code=409, detail=f"Só contrato assinado (situação atual: {c.status})")
    q = svc.rescission_quote(ctx.db, c, retention_pct)
    return RescissionQuoteOut(**q.__dict__)


@router.get("/contracts/{contract_id}/rescission", response_model=RescissionOut | None)
def get_rescission(
    contract_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.CONTRACTS_VIEW))
) -> RescissionOut | None:
    _contract(ctx, contract_id)
    row = ctx.db.execute(
        select(Rescission, User.full_name)
        .outerjoin(User, User.id == Rescission.created_by)
        .where(Rescission.contract_id == contract_id, Rescission.tenant_id == ctx.tenant_id)
    ).one_or_none()
    if row is None:
        return None
    r, author = row
    return RescissionOut(
        paid_total=r.paid_total,
        retention_pct=r.retention_pct,
        retention_amount=r.retention_amount,
        refund_amount=r.refund_amount,
        payable_id=r.payable_id,
        reason=r.reason,
        author_name=author,
        created_at=r.created_at,
    )


@router.post("/contracts/{contract_id}/rescind", response_model=RescissionOut)
def rescind_contract(
    contract_id: uuid.UUID, body: RescindIn, ctx: AuthContext = Depends(require(Perm.CONTRACTS_SIGN))
) -> RescissionOut:
    """Distrato: exige quem pode assinar contratos (owner/admin)."""
    _contract(ctx, contract_id)
    due = body.refund_due_date or today() + timedelta(days=30)
    if due < today():
        raise HTTPException(status_code=422, detail="Vencimento da devolução não pode estar no passado")
    try:
        svc.rescind(
            ctx.db,
            tenant_id=ctx.tenant_id,
            contract_id=contract_id,
            retention_pct=body.retention_pct,
            refund_due_date=due,
            actor=ctx.user.id,
            reason=body.reason,
        )
    except svc.ChangeError as e:
        raise _business(e) from None
    out = get_rescission(contract_id, ctx)
    assert out is not None
    return out
