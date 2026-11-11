"""Fase 3a: parcelas, contas a receber, recebimentos e estornos."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.models import (
    Block,
    Customer,
    Development,
    Installment,
    Lot,
    Payment,
    PaymentReversal,
    SalesContract,
    User,
)
from app.services import receivables as svc

router = APIRouter(tags=["financeiro"])

Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
Method = Literal["pix", "boleto", "transferencia", "dinheiro", "cartao", "cheque", "outro"]
ViewStatus = Literal["aberta", "vencida", "a_vencer", "paga"]


# ─────────────────────────── DTOs ───────────────────────────


class InstallmentOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    contract_number: str
    number: int
    due_date: date
    principal: Decimal
    interest: Decimal
    amount: Decimal
    paid_principal: Decimal
    remaining: Decimal
    status: str  # aberta | paga | cancelada
    overdue: bool
    days_late: int
    paid_at: date | None
    customer_id: uuid.UUID
    customer_name: str
    lot_id: uuid.UUID
    lot_number: str
    block_code: str
    development_id: uuid.UUID
    development_name: str


class QuoteOut(BaseModel):
    on: date
    remaining: Decimal
    late_fee: Decimal
    late_interest: Decimal
    days_late: int
    total: Decimal
    late_fee_pct: Decimal
    late_interest_monthly_pct: Decimal


class PaymentOut(BaseModel):
    id: uuid.UUID
    received_on: date
    amount: Decimal
    principal: Decimal
    late_fee: Decimal
    late_interest: Decimal
    days_late: int
    method: str
    notes: str | None
    created_by_name: str | None
    created_at: datetime
    reversed: bool
    reversal_reason: str | None
    reversed_at: datetime | None


class InstallmentDetailOut(InstallmentOut):
    quote: QuoteOut
    payments: list[PaymentOut]


class PaymentIn(BaseModel):
    amount: Money
    received_on: date | None = None  # padrão: hoje
    method: Method = "pix"
    notes: str | None = Field(default=None, max_length=2000)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SummaryOut(BaseModel):
    open_total: Decimal
    open_count: int
    overdue_total: Decimal
    overdue_count: int
    due_30_total: Decimal
    due_30_count: int
    received_month: Decimal  # líquido de estornos
    received_month_late_charges: Decimal


# ─────────────────────────── helpers ───────────────────────────


def _query(ctx: AuthContext) -> Any:
    return (
        select(
            Installment,
            SalesContract.number,
            SalesContract.customer_id,
            Customer.name,
            Lot.id,
            Lot.number,
            Block.code,
            Development.id,
            Development.name,
        )
        .join(SalesContract, SalesContract.id == Installment.contract_id)
        .join(Customer, Customer.id == SalesContract.customer_id)
        .join(Lot, Lot.id == SalesContract.lot_id)
        .join(Block, Block.id == Lot.block_id)
        .join(Development, Development.id == Lot.development_id)
        .where(Installment.tenant_id == ctx.tenant_id)
    )


def _out(row: Any, on: date) -> InstallmentOut:
    i, c_number, customer_id, customer_name, lot_id, lot_number, block_code, dev_id, dev_name = row
    overdue = i.status == "aberta" and i.due_date < on
    return InstallmentOut(
        id=i.id,
        contract_id=i.contract_id,
        contract_number=c_number,
        number=i.number,
        due_date=i.due_date,
        principal=i.principal,
        interest=i.interest,
        amount=i.amount,
        paid_principal=i.paid_principal,
        remaining=i.amount - i.paid_principal,
        status=i.status,
        overdue=overdue,
        days_late=(on - i.due_date).days if overdue else 0,
        paid_at=i.paid_at,
        customer_id=customer_id,
        customer_name=customer_name,
        lot_id=lot_id,
        lot_number=lot_number,
        block_code=block_code,
        development_id=dev_id,
        development_name=dev_name,
    )


def _business(e: svc.ReceivableError) -> HTTPException:
    return HTTPException(
        status_code=404 if isinstance(e, svc.ReceivableNotFoundError) else 409, detail=str(e)
    )


def _detail(ctx: AuthContext, installment_id: uuid.UUID) -> InstallmentDetailOut:
    on = svc.today()
    row = ctx.db.execute(_query(ctx).where(Installment.id == installment_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Parcela não encontrada")
    inst: Installment = row[0]
    q = svc.quote_installment(ctx.db, inst, on)
    ch = svc.charges_for(ctx.db, inst)
    pays = ctx.db.execute(
        select(Payment, User.full_name, PaymentReversal)
        .outerjoin(User, User.id == Payment.created_by)
        .outerjoin(PaymentReversal, PaymentReversal.payment_id == Payment.id)
        .where(Payment.installment_id == inst.id, Payment.tenant_id == ctx.tenant_id)
        .order_by(Payment.created_at.desc())
    ).all()
    return InstallmentDetailOut(
        **_out(row, on).model_dump(),
        quote=QuoteOut(
            on=on,
            remaining=q.principal,
            late_fee=q.late_fee,
            late_interest=q.late_interest,
            days_late=q.days_late,
            total=q.total,
            late_fee_pct=ch.late_fee_pct,
            late_interest_monthly_pct=ch.late_interest_monthly_pct,
        ),
        payments=[
            PaymentOut(
                id=p.id,
                received_on=p.received_on,
                amount=p.amount,
                principal=p.principal,
                late_fee=p.late_fee,
                late_interest=p.late_interest,
                days_late=p.days_late,
                method=p.method,
                notes=p.notes,
                created_by_name=author,
                created_at=p.created_at,
                reversed=rev is not None,
                reversal_reason=rev.reason if rev else None,
                reversed_at=rev.created_at if rev else None,
            )
            for p, author, rev in pays
        ],
    )


# ─────────────────────────── rotas ───────────────────────────


@router.get("/contracts/{contract_id}/installments", response_model=list[InstallmentOut])
def contract_installments(
    contract_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> list[InstallmentOut]:
    if (
        ctx.db.scalar(
            select(SalesContract.id).where(
                SalesContract.id == contract_id, SalesContract.tenant_id == ctx.tenant_id
            )
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    svc.ensure_schedule(ctx.db, ctx.tenant_id, contract_id)
    on = svc.today()
    rows = ctx.db.execute(
        _query(ctx).where(Installment.contract_id == contract_id).order_by(Installment.number)
    ).all()
    return [_out(r, on) for r in rows]


@router.get("/installments", response_model=list[InstallmentOut])
def list_installments(
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
    status_: ViewStatus | None = Query(default=None, alias="status"),
    due_from: date | None = None,
    due_to: date | None = None,
    development_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=300, ge=1, le=1000),
) -> list[InstallmentOut]:
    on = svc.today()
    stmt = _query(ctx)
    if status_ == "aberta":
        stmt = stmt.where(Installment.status == "aberta")
    elif status_ == "vencida":
        stmt = stmt.where(Installment.status == "aberta", Installment.due_date < on)
    elif status_ == "a_vencer":
        stmt = stmt.where(Installment.status == "aberta", Installment.due_date >= on)
    elif status_ == "paga":
        stmt = stmt.where(Installment.status == "paga")
    if due_from:
        stmt = stmt.where(Installment.due_date >= due_from)
    if due_to:
        stmt = stmt.where(Installment.due_date <= due_to)
    if development_id:
        stmt = stmt.where(Lot.development_id == development_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Customer.name).like(like), SalesContract.number.like(f"%{q}%")))
    order = Installment.paid_at.desc() if status_ == "paga" else Installment.due_date
    rows = ctx.db.execute(stmt.order_by(order, Installment.number).limit(limit)).all()
    return [_out(r, on) for r in rows]


@router.get("/receivables/summary", response_model=SummaryOut)
def receivables_summary(ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))) -> SummaryOut:
    on = svc.today()
    rem = Installment.amount - Installment.paid_principal
    zero = Decimal("0")
    open_ = Installment.status == "aberta"
    row = ctx.db.execute(
        select(
            func.coalesce(func.sum(case((open_, rem), else_=zero)), zero),
            func.count().filter(open_),
            func.coalesce(func.sum(case((open_ & (Installment.due_date < on), rem), else_=zero)), zero),
            func.count().filter(open_ & (Installment.due_date < on)),
            func.coalesce(
                func.sum(
                    case(
                        (open_ & Installment.due_date.between(on, on + timedelta(days=30)), rem),
                        else_=zero,
                    )
                ),
                zero,
            ),
            func.count().filter(open_ & Installment.due_date.between(on, on + timedelta(days=30))),
        ).where(Installment.tenant_id == ctx.tenant_id)
    ).one()
    month_start = on.replace(day=1)
    paid = ctx.db.execute(
        select(
            func.coalesce(func.sum(Payment.amount), zero),
            func.coalesce(func.sum(Payment.late_fee + Payment.late_interest), zero),
        )
        .outerjoin(PaymentReversal, PaymentReversal.payment_id == Payment.id)
        .where(
            Payment.tenant_id == ctx.tenant_id,
            Payment.received_on >= month_start,
            PaymentReversal.id.is_(None),
        )
    ).one()
    return SummaryOut(
        open_total=row[0],
        open_count=row[1],
        overdue_total=row[2],
        overdue_count=row[3],
        due_30_total=row[4],
        due_30_count=row[5],
        received_month=paid[0],
        received_month_late_charges=paid[1],
    )


@router.get("/installments/{installment_id}", response_model=InstallmentDetailOut)
def get_installment(
    installment_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> InstallmentDetailOut:
    return _detail(ctx, installment_id)


@router.get("/installments/{installment_id}/quote", response_model=QuoteOut)
def quote_installment(
    installment_id: uuid.UUID,
    on: date | None = None,
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
) -> QuoteOut:
    """Valor para quitar a parcela numa data (com multa e juros de mora se em atraso)."""
    inst = ctx.db.scalar(
        select(Installment).where(Installment.id == installment_id, Installment.tenant_id == ctx.tenant_id)
    )
    if inst is None:
        raise HTTPException(status_code=404, detail="Parcela não encontrada")
    day = on or svc.today()
    q = svc.quote_installment(ctx.db, inst, day)
    ch = svc.charges_for(ctx.db, inst)
    return QuoteOut(
        on=day,
        remaining=q.principal,
        late_fee=q.late_fee,
        late_interest=q.late_interest,
        days_late=q.days_late,
        total=q.total,
        late_fee_pct=ch.late_fee_pct,
        late_interest_monthly_pct=ch.late_interest_monthly_pct,
    )


@router.post("/installments/{installment_id}/payments", response_model=InstallmentDetailOut, status_code=201)
def register_payment(
    installment_id: uuid.UUID, body: PaymentIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> InstallmentDetailOut:
    try:
        svc.register_payment(
            ctx.db,
            tenant_id=ctx.tenant_id,
            installment_id=installment_id,
            amount=body.amount,
            received_on=body.received_on or svc.today(),
            method=body.method,
            notes=body.notes,
            actor=ctx.user.id,
        )
    except svc.ReceivableError as e:
        raise _business(e) from None
    return _detail(ctx, installment_id)


@router.post("/payments/{payment_id}/reverse", response_model=InstallmentDetailOut)
def reverse_payment(
    payment_id: uuid.UUID, body: ReverseIn, ctx: AuthContext = Depends(require(Perm.FINANCE_REVERSE))
) -> InstallmentDetailOut:
    try:
        svc.reverse_payment(
            ctx.db, tenant_id=ctx.tenant_id, payment_id=payment_id, reason=body.reason, actor=ctx.user.id
        )
    except svc.ReceivableError as e:
        raise _business(e) from None
    inst_id = ctx.db.scalar(select(Payment.installment_id).where(Payment.id == payment_id))
    assert inst_id is not None
    return _detail(ctx, inst_id)
