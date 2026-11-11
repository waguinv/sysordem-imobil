"""Fase 3b: comissões, repasses ao proprietário, contas a pagar e regras financeiras."""

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
    Broker,
    Development,
    LandOwner,
    Participation,
    Payable,
    PayableEvent,
    SalesContract,
    User,
)
from app.services import audit
from app.services import payables as svc
from app.services.finance import money
from app.services.receivables import today

router = APIRouter(tags=["contas a pagar"])

Money = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
Pct = Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]
Kind = Literal["comissao", "proprietario"]
Category = Literal[
    "comissao", "proprietario", "fornecedor", "obra", "imposto", "marketing", "outro", "devolucao"
]
Method = Literal["pix", "boleto", "transferencia", "dinheiro", "cartao", "cheque", "outro"]


# ─────────────────────────── regras ───────────────────────────


class FinanceSettingsIn(BaseModel):
    commission_basis: Literal["recebimento", "assinatura"]
    default_commission_pct: Pct
    reason: str = Field(min_length=3, max_length=500)


class FinanceSettingsOut(BaseModel):
    commission_basis: str
    default_commission_pct: Decimal
    is_default: bool


class OwnerShareOut(BaseModel):
    development_id: uuid.UUID
    development_name: str
    land_owner_id: uuid.UUID | None
    land_owner_name: str | None
    owner_share_pct: Decimal


class OwnerShareIn(BaseModel):
    owner_share_pct: Pct
    reason: str = Field(min_length=3, max_length=500)


@router.get("/settings/finance", response_model=FinanceSettingsOut)
def get_finance_settings(ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))) -> FinanceSettingsOut:
    s = svc.get_settings(ctx.db, ctx.tenant_id)
    return FinanceSettingsOut(
        commission_basis=s.commission_basis,
        default_commission_pct=s.default_commission_pct,
        is_default=s.is_default,
    )


@router.put("/settings/finance", response_model=FinanceSettingsOut)
def put_finance_settings(
    body: FinanceSettingsIn, ctx: AuthContext = Depends(require(Perm.SETTINGS_MANAGE))
) -> FinanceSettingsOut:
    s = svc.save_settings(
        ctx.db,
        ctx.tenant_id,
        basis=body.commission_basis,
        default_pct=body.default_commission_pct,
        actor=ctx.user.id,
        reason=body.reason,
    )
    return FinanceSettingsOut(
        commission_basis=s.commission_basis, default_commission_pct=s.default_commission_pct, is_default=False
    )


@router.get("/owner-shares", response_model=list[OwnerShareOut])
def list_owner_shares(ctx: AuthContext = Depends(require(Perm.OWNERS_VIEW))) -> list[OwnerShareOut]:
    rows = ctx.db.execute(
        select(Development, LandOwner.name)
        .outerjoin(LandOwner, LandOwner.id == Development.land_owner_id)
        .where(Development.tenant_id == ctx.tenant_id)
        .order_by(Development.name)
    ).all()
    return [
        OwnerShareOut(
            development_id=d.id,
            development_name=d.name,
            land_owner_id=d.land_owner_id,
            land_owner_name=owner,
            owner_share_pct=d.owner_share_pct,
        )
        for d, owner in rows
    ]


@router.put("/developments/{development_id}/owner-share", response_model=OwnerShareOut)
def put_owner_share(
    development_id: uuid.UUID, body: OwnerShareIn, ctx: AuthContext = Depends(require(Perm.OWNERS_MANAGE))
) -> OwnerShareOut:
    d = ctx.db.scalar(
        select(Development).where(Development.id == development_id, Development.tenant_id == ctx.tenant_id)
    )
    if d is None:
        raise HTTPException(status_code=404, detail="Empreendimento não encontrado")
    if body.owner_share_pct > 0 and d.land_owner_id is None:
        raise HTTPException(status_code=422, detail="Defina o proprietário da área do empreendimento antes")
    before = d.owner_share_pct
    d.owner_share_pct = body.owner_share_pct
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="development.owner_share",
        entity="development",
        entity_id=d.id,
        reason=body.reason,
        data={"before": str(before), "after": str(body.owner_share_pct)},
    )
    owner = ctx.db.scalar(select(LandOwner.name).where(LandOwner.id == d.land_owner_id))
    return OwnerShareOut(
        development_id=d.id,
        development_name=d.name,
        land_owner_id=d.land_owner_id,
        land_owner_name=owner,
        owner_share_pct=d.owner_share_pct,
    )


# ─────────────────────────── créditos (comissões e repasses) ───────────────────────────


class BalanceOut(BaseModel):
    kind: str
    beneficiary_id: uuid.UUID
    beneficiary_name: str
    pending_total: Decimal
    pending_count: int
    paid_total: Decimal  # créditos já ligados a contas pagas


class ParticipationOut(BaseModel):
    id: uuid.UUID
    kind: str
    beneficiary_id: uuid.UUID
    beneficiary_name: str
    contract_id: uuid.UUID
    contract_number: str
    base: Decimal
    pct: Decimal
    amount: Decimal
    is_reversal: bool
    on_signature: bool
    payable_id: uuid.UUID | None
    payable_status: str | None
    created_at: datetime


class SettleIn(BaseModel):
    kind: Kind
    beneficiary_id: uuid.UUID
    due_date: date | None = None  # padrão: hoje + 7 dias


def _beneficiary_cols(kind: str) -> tuple[Any, Any, Any]:
    if kind == "comissao":
        return Participation.broker_id, Broker.name, Broker
    return Participation.land_owner_id, LandOwner.name, LandOwner


@router.get("/participations/balances", response_model=list[BalanceOut])
def participation_balances(
    kind: Kind, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> list[BalanceOut]:
    col, name_col, model = _beneficiary_cols(kind)
    zero = Decimal("0")
    pending = Participation.payable_id.is_(None)
    rows = ctx.db.execute(
        select(
            col,
            name_col,
            func.coalesce(func.sum(case((pending, Participation.amount), else_=zero)), zero),
            func.count().filter(pending),
            func.coalesce(func.sum(case((Payable.status == "paga", Participation.amount), else_=zero)), zero),
        )
        .join(model, model.id == col)
        .outerjoin(Payable, Payable.id == Participation.payable_id)
        .where(Participation.tenant_id == ctx.tenant_id, Participation.kind == kind)
        .group_by(col, name_col)
        .order_by(name_col)
    ).all()
    return [
        BalanceOut(
            kind=kind,
            beneficiary_id=b,
            beneficiary_name=n,
            pending_total=money(pt),
            pending_count=pc,
            paid_total=money(paid),
        )
        for b, n, pt, pc, paid in rows
    ]


@router.get("/participations", response_model=list[ParticipationOut])
def list_participations(
    kind: Kind,
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
    beneficiary_id: uuid.UUID | None = None,
    pending: bool | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[ParticipationOut]:
    col, name_col, model = _beneficiary_cols(kind)
    stmt = (
        select(Participation, name_col, SalesContract.number, Payable.status)
        .join(model, model.id == col)
        .join(SalesContract, SalesContract.id == Participation.contract_id)
        .outerjoin(Payable, Payable.id == Participation.payable_id)
        .where(Participation.tenant_id == ctx.tenant_id, Participation.kind == kind)
    )
    if beneficiary_id:
        stmt = stmt.where(col == beneficiary_id)
    if pending is not None:
        stmt = stmt.where(
            Participation.payable_id.is_(None) if pending else Participation.payable_id.is_not(None)
        )
    rows = ctx.db.execute(stmt.order_by(Participation.created_at.desc()).limit(limit)).all()
    return [
        ParticipationOut(
            id=p.id,
            kind=p.kind,
            beneficiary_id=p.broker_id or p.land_owner_id,
            beneficiary_name=name,
            contract_id=p.contract_id,
            contract_number=number,
            base=p.base,
            pct=p.pct,
            amount=p.amount,
            is_reversal=p.reverses_id is not None,
            on_signature=p.payment_id is None,
            payable_id=p.payable_id,
            payable_status=pstatus,
            created_at=p.created_at,
        )
        for p, name, number, pstatus in rows
    ]


# ─────────────────────────── contas a pagar ───────────────────────────


class PayableIn(BaseModel):
    description: str = Field(min_length=3, max_length=240)
    category: Literal["fornecedor", "obra", "imposto", "marketing", "outro"]  # comissão/repasse: via acerto
    payee_name: str = Field(min_length=2, max_length=160)
    development_id: uuid.UUID | None = None
    due_date: date
    amount: Money
    notes: str | None = Field(default=None, max_length=2000)


class PayIn(BaseModel):
    paid_on: date | None = None
    method: Method = "pix"
    notes: str | None = Field(default=None, max_length=500)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PayableOut(BaseModel):
    id: uuid.UUID
    description: str
    category: str
    payee_name: str
    development_id: uuid.UUID | None
    development_name: str | None
    due_date: date
    amount: Decimal
    status: str
    overdue: bool
    paid_on: date | None
    method: str | None
    notes: str | None
    created_at: datetime


class PayableEventOut(BaseModel):
    kind: str
    event_date: date | None
    method: str | None
    reason: str | None
    author_name: str | None
    created_at: datetime


class PayableDetailOut(PayableOut):
    events: list[PayableEventOut]
    participations_count: int


class PayablesSummaryOut(BaseModel):
    open_total: Decimal
    open_count: int
    overdue_total: Decimal
    overdue_count: int
    due_30_total: Decimal
    paid_month: Decimal
    pending_participations: Decimal  # comissões/repasses ainda sem conta a pagar


def _business(e: svc.PayableError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(e, svc.PayableNotFoundError) else 409, detail=str(e))


def _query(ctx: AuthContext) -> Any:
    return (
        select(Payable, Development.name)
        .outerjoin(Development, Development.id == Payable.development_id)
        .where(Payable.tenant_id == ctx.tenant_id)
    )


def _out(row: Any, on: date) -> PayableOut:
    p, dev_name = row
    return PayableOut(
        id=p.id,
        description=p.description,
        category=p.category,
        payee_name=p.payee_name,
        development_id=p.development_id,
        development_name=dev_name,
        due_date=p.due_date,
        amount=p.amount,
        status=p.status,
        overdue=p.status == "aberta" and p.due_date < on,
        paid_on=p.paid_on,
        method=p.method,
        notes=p.notes,
        created_at=p.created_at,
    )


def _detail(ctx: AuthContext, payable_id: uuid.UUID) -> PayableDetailOut:
    row = ctx.db.execute(_query(ctx).where(Payable.id == payable_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Conta a pagar não encontrada")
    events = ctx.db.execute(
        select(PayableEvent, User.full_name)
        .outerjoin(User, User.id == PayableEvent.created_by)
        .where(PayableEvent.payable_id == payable_id, PayableEvent.tenant_id == ctx.tenant_id)
        .order_by(PayableEvent.seq.desc())
    ).all()
    count = ctx.db.scalar(select(func.count()).where(Participation.payable_id == payable_id)) or 0
    return PayableDetailOut(
        **_out(row, today()).model_dump(),
        events=[
            PayableEventOut(
                kind=e.kind,
                event_date=e.event_date,
                method=e.method,
                reason=e.reason,
                author_name=author,
                created_at=e.created_at,
            )
            for e, author in events
        ],
        participations_count=count,
    )


@router.get("/payables/summary", response_model=PayablesSummaryOut)
def payables_summary(ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))) -> PayablesSummaryOut:
    on = today()
    zero = Decimal("0")
    open_ = Payable.status == "aberta"
    row = ctx.db.execute(
        select(
            func.coalesce(func.sum(case((open_, Payable.amount), else_=zero)), zero),
            func.count().filter(open_),
            func.coalesce(
                func.sum(case((open_ & (Payable.due_date < on), Payable.amount), else_=zero)), zero
            ),
            func.count().filter(open_ & (Payable.due_date < on)),
            func.coalesce(
                func.sum(
                    case(
                        (open_ & Payable.due_date.between(on, on + timedelta(days=30)), Payable.amount),
                        else_=zero,
                    )
                ),
                zero,
            ),
            func.coalesce(
                func.sum(
                    case(
                        ((Payable.status == "paga") & (Payable.paid_on >= on.replace(day=1)), Payable.amount),
                        else_=zero,
                    )
                ),
                zero,
            ),
        ).where(Payable.tenant_id == ctx.tenant_id)
    ).one()
    return PayablesSummaryOut(
        open_total=money(row[0]),
        open_count=row[1],
        overdue_total=money(row[2]),
        overdue_count=row[3],
        due_30_total=money(row[4]),
        paid_month=money(row[5]),
        pending_participations=money(svc.pending_total(ctx.db, ctx.tenant_id)),
    )


@router.get("/payables", response_model=list[PayableOut])
def list_payables(
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
    status_: Literal["aberta", "vencida", "paga", "cancelada"] | None = Query(default=None, alias="status"),
    category: Category | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=300, ge=1, le=1000),
) -> list[PayableOut]:
    on = today()
    stmt = _query(ctx)
    if status_ == "vencida":
        stmt = stmt.where(Payable.status == "aberta", Payable.due_date < on)
    elif status_:
        stmt = stmt.where(Payable.status == status_)
    if category:
        stmt = stmt.where(Payable.category == category)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(Payable.payee_name).like(like), func.lower(Payable.description).like(like))
        )
    order = Payable.paid_on.desc() if status_ == "paga" else Payable.due_date
    rows = ctx.db.execute(stmt.order_by(order, Payable.created_at).limit(limit)).all()
    return [_out(r, on) for r in rows]


@router.post("/payables", response_model=PayableDetailOut, status_code=201)
def create_payable(
    body: PayableIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> PayableDetailOut:
    if (
        body.development_id
        and ctx.db.scalar(
            select(Development.id).where(
                Development.id == body.development_id, Development.tenant_id == ctx.tenant_id
            )
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="Empreendimento não encontrado")
    pay = svc.create_payable(ctx.db, tenant_id=ctx.tenant_id, actor=ctx.user.id, **body.model_dump())
    return _detail(ctx, pay.id)


@router.get("/payables/{payable_id}", response_model=PayableDetailOut)
def get_payable(
    payable_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> PayableDetailOut:
    return _detail(ctx, payable_id)


@router.post("/payables/{payable_id}/pay", response_model=PayableDetailOut)
def pay_payable(
    payable_id: uuid.UUID, body: PayIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> PayableDetailOut:
    try:
        svc.pay_payable(
            ctx.db,
            tenant_id=ctx.tenant_id,
            payable_id=payable_id,
            paid_on=body.paid_on or today(),
            method=body.method,
            today=today(),
            actor=ctx.user.id,
            reason=body.notes,
        )
    except svc.PayableError as e:
        raise _business(e) from None
    return _detail(ctx, payable_id)


@router.post("/payables/{payable_id}/reverse", response_model=PayableDetailOut)
def reverse_payable(
    payable_id: uuid.UUID, body: ReasonIn, ctx: AuthContext = Depends(require(Perm.FINANCE_REVERSE))
) -> PayableDetailOut:
    try:
        svc.reverse_payable(
            ctx.db, tenant_id=ctx.tenant_id, payable_id=payable_id, actor=ctx.user.id, reason=body.reason
        )
    except svc.PayableError as e:
        raise _business(e) from None
    return _detail(ctx, payable_id)


@router.post("/payables/{payable_id}/cancel", response_model=PayableDetailOut)
def cancel_payable(
    payable_id: uuid.UUID, body: ReasonIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> PayableDetailOut:
    try:
        svc.cancel_payable(
            ctx.db, tenant_id=ctx.tenant_id, payable_id=payable_id, actor=ctx.user.id, reason=body.reason
        )
    except svc.PayableError as e:
        raise _business(e) from None
    return _detail(ctx, payable_id)


@router.post("/participations/settle", response_model=PayableDetailOut, status_code=201)
def settle_participations(
    body: SettleIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> PayableDetailOut:
    try:
        pay = svc.settle(
            ctx.db,
            tenant_id=ctx.tenant_id,
            kind=body.kind,
            beneficiary_id=body.beneficiary_id,
            due_date=body.due_date or today() + timedelta(days=7),
            actor=ctx.user.id,
        )
    except svc.PayableError as e:
        raise _business(e) from None
    return _detail(ctx, pay.id)
