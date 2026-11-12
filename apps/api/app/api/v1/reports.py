"""Fase 3c: relatórios (fluxo de caixa, inadimplência e vendas). Somente leitura."""

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, literal_column, select

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.models import (
    Block,
    ContractVersion,
    Customer,
    Development,
    Installment,
    Lot,
    Payable,
    Payment,
    PaymentReversal,
    Rescission,
    SalesContract,
)
from app.services.finance import add_months, money
from app.services.receivables import today

router = APIRouter(prefix="/reports", tags=["relatórios"])
ZERO = Decimal("0.00")


def _month(d: Any) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ─────────────────────────── fluxo de caixa ───────────────────────────


class CashFlowMonth(BaseModel):
    month: str  # AAAA-MM
    received: Decimal  # recebimentos (com multa/mora), líquido de estornos
    paid: Decimal  # contas pagas
    net: Decimal
    expected_in: Decimal  # parcelas em aberto com vencimento no mês
    expected_out: Decimal  # contas a pagar em aberto com vencimento no mês


class CashFlowOut(BaseModel):
    months: list[CashFlowMonth]
    overdue_in_before: Decimal  # parcelas vencidas antes do período
    overdue_out_before: Decimal


@router.get("/cash-flow", response_model=CashFlowOut)
def cash_flow(
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
    months_back: int = Query(default=6, ge=0, le=36),
    months_ahead: int = Query(default=6, ge=0, le=36),
) -> CashFlowOut:
    start = add_months(today().replace(day=1), -months_back)
    end = add_months(today().replace(day=1), months_ahead + 1)  # exclusivo
    t = ctx.tenant_id
    data: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))

    rec = ctx.db.execute(
        select(func.date_trunc("month", Payment.received_on), func.sum(Payment.amount))
        .outerjoin(PaymentReversal, PaymentReversal.payment_id == Payment.id)
        .where(
            Payment.tenant_id == t,
            PaymentReversal.id.is_(None),
            Payment.received_on >= start,
            Payment.received_on < end,
        )
        .group_by(literal_column("1"))
    ).all()
    for m, v in rec:
        data[_month(m)]["received"] = v
    paid = ctx.db.execute(
        select(func.date_trunc("month", Payable.paid_on), func.sum(Payable.amount))
        .where(
            Payable.tenant_id == t, Payable.status == "paga", Payable.paid_on >= start, Payable.paid_on < end
        )
        .group_by(literal_column("1"))
    ).all()
    for m, v in paid:
        data[_month(m)]["paid"] = v
    rem = Installment.amount - Installment.paid_principal
    exp_in = ctx.db.execute(
        select(func.date_trunc("month", Installment.due_date), func.sum(rem))
        .where(
            Installment.tenant_id == t,
            Installment.status == "aberta",
            Installment.due_date >= start,
            Installment.due_date < end,
        )
        .group_by(literal_column("1"))
    ).all()
    for m, v in exp_in:
        data[_month(m)]["expected_in"] = v
    exp_out = ctx.db.execute(
        select(func.date_trunc("month", Payable.due_date), func.sum(Payable.amount))
        .where(
            Payable.tenant_id == t,
            Payable.status == "aberta",
            Payable.due_date >= start,
            Payable.due_date < end,
        )
        .group_by(literal_column("1"))
    ).all()
    for m, v in exp_out:
        data[_month(m)]["expected_out"] = v

    before_in = ctx.db.scalar(
        select(func.coalesce(func.sum(rem), 0)).where(
            Installment.tenant_id == t, Installment.status == "aberta", Installment.due_date < start
        )
    )
    before_out = ctx.db.scalar(
        select(func.coalesce(func.sum(Payable.amount), 0)).where(
            Payable.tenant_id == t, Payable.status == "aberta", Payable.due_date < start
        )
    )
    months: list[CashFlowMonth] = []
    cur = start
    while cur < end:
        d = data[_month(cur)]
        months.append(
            CashFlowMonth(
                month=_month(cur),
                received=money(d["received"]),
                paid=money(d["paid"]),
                net=money(d["received"] - d["paid"]),
                expected_in=money(d["expected_in"]),
                expected_out=money(d["expected_out"]),
            )
        )
        cur = add_months(cur, 1)
    return CashFlowOut(
        months=months,
        overdue_in_before=money(Decimal(before_in or 0)),
        overdue_out_before=money(Decimal(before_out or 0)),
    )


# ─────────────────────────── inadimplência ───────────────────────────

BUCKETS = (("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6))


class DelinquentContract(BaseModel):
    contract_id: uuid.UUID
    contract_number: str
    customer_id: uuid.UUID
    customer_name: str
    customer_phone: str | None
    development_name: str
    block_code: str
    lot_number: str
    overdue_count: int
    overdue_amount: Decimal  # sem encargos
    oldest_due: date
    days_late: int
    bucket: str


class DelinquencyOut(BaseModel):
    on: date
    total: Decimal
    contracts: int
    buckets: dict[str, Decimal]
    items: list[DelinquentContract]


def _bucket(days: int) -> str:
    return next(name for name, lo, hi in BUCKETS if lo <= days <= hi)


@router.get("/delinquency", response_model=DelinquencyOut)
def delinquency(
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)), development_id: uuid.UUID | None = None
) -> DelinquencyOut:
    on = today()
    rem = Installment.amount - Installment.paid_principal
    stmt = (
        select(
            SalesContract.id,
            SalesContract.number,
            Customer.id,
            Customer.name,
            Customer.phone,
            Development.name,
            Block.code,
            Lot.number,
            func.count(Installment.id),
            func.sum(rem),
            func.min(Installment.due_date),
        )
        .join(SalesContract, SalesContract.id == Installment.contract_id)
        .join(Customer, Customer.id == SalesContract.customer_id)
        .join(Lot, Lot.id == SalesContract.lot_id)
        .join(Block, Block.id == Lot.block_id)
        .join(Development, Development.id == Lot.development_id)
        .where(
            Installment.tenant_id == ctx.tenant_id, Installment.status == "aberta", Installment.due_date < on
        )
        .group_by(
            SalesContract.id,
            SalesContract.number,
            Customer.id,
            Customer.name,
            Customer.phone,
            Development.name,
            Block.code,
            Lot.number,
        )
        .order_by(func.min(Installment.due_date))
    )
    if development_id:
        stmt = stmt.where(Lot.development_id == development_id)
    items: list[DelinquentContract] = []
    buckets = {name: ZERO for name, _, _ in BUCKETS}
    for cid, num, cust_id, cust, phone, dev, block, lot, count, amount, oldest in ctx.db.execute(stmt).all():
        days = (on - oldest).days
        b = _bucket(days)
        buckets[b] += amount
        items.append(
            DelinquentContract(
                contract_id=cid,
                contract_number=num,
                customer_id=cust_id,
                customer_name=cust,
                customer_phone=phone,
                development_name=dev,
                block_code=block,
                lot_number=lot,
                overdue_count=count,
                overdue_amount=amount,
                oldest_due=oldest,
                days_late=days,
                bucket=b,
            )
        )
    return DelinquencyOut(
        on=on,
        total=money(sum((i.overdue_amount for i in items), ZERO)),
        contracts=len(items),
        buckets={k: money(v) for k, v in buckets.items()},
        items=items,
    )


# ─────────────────────────── vendas ───────────────────────────


class SalesRow(BaseModel):
    development_id: uuid.UUID
    development_name: str
    contracts: int
    total_value: Decimal
    down_payments: Decimal
    rescissions: int


class SalesOut(BaseModel):
    date_from: date
    date_to: date
    rows: list[SalesRow]
    contracts: int
    total_value: Decimal


@router.get("/sales", response_model=SalesOut)
def sales(
    ctx: AuthContext = Depends(require(Perm.CONTRACTS_VIEW)),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
) -> SalesOut:
    """Contratos assinados no período (pelo valor da versão assinada) e distratos no período."""
    d_to = date_to or today()
    d_from = date_from or d_to.replace(month=1, day=1)
    signed_on = func.date(func.timezone("America/Sao_Paulo", SalesContract.signed_at))
    rows = ctx.db.execute(
        select(
            Development.id,
            Development.name,
            func.count(SalesContract.id),
            func.coalesce(func.sum(ContractVersion.total_price), 0),
            func.coalesce(func.sum(ContractVersion.down_payment), 0),
        )
        .join(
            ContractVersion,
            (ContractVersion.contract_id == SalesContract.id)
            & (ContractVersion.version == SalesContract.current_version),
        )
        .join(Lot, Lot.id == SalesContract.lot_id)
        .join(Development, Development.id == Lot.development_id)
        .where(
            SalesContract.tenant_id == ctx.tenant_id,
            SalesContract.signed_at.is_not(None),
            signed_on >= d_from,
            signed_on <= d_to,
        )
        .group_by(Development.id, Development.name)
        .order_by(Development.name)
    ).all()
    resc_on = func.date(func.timezone("America/Sao_Paulo", Rescission.created_at))
    resc = dict(
        ctx.db.execute(
            select(Lot.development_id, func.count(Rescission.id))
            .join(SalesContract, SalesContract.id == Rescission.contract_id)
            .join(Lot, Lot.id == SalesContract.lot_id)
            .where(Rescission.tenant_id == ctx.tenant_id, resc_on >= d_from, resc_on <= d_to)
            .group_by(Lot.development_id)
        ).all()
    )
    out = [
        SalesRow(
            development_id=dev_id,
            development_name=name,
            contracts=count,
            total_value=money(Decimal(total)),
            down_payments=money(Decimal(down)),
            rescissions=resc.get(dev_id, 0),
        )
        for dev_id, name, count, total, down in rows
    ]
    return SalesOut(
        date_from=d_from,
        date_to=d_to,
        rows=out,
        contracts=sum(r.contracts for r in out),
        total_value=money(sum((r.total_value for r in out), ZERO)),
    )
