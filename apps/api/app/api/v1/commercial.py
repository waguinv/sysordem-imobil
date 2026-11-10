"""Fase 2a: compradores, corretores e reservas."""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.core.config import get_settings
from app.models import Block, Broker, Customer, Development, Lot, Reservation
from app.services import audit
from app.services import reservations as svc

router = APIRouter(tags=["comercial"])

Name = Annotated[str, Field(min_length=2, max_length=160)]


def _digits_doc(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    d = re.sub(r"\D", "", v)
    if len(d) not in (11, 14):
        raise ValueError("CPF (11 dígitos) ou CNPJ (14 dígitos)")
    return d


# ─────────────────────────── DTOs ───────────────────────────


class CustomerIn(BaseModel):
    name: Name
    document: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    notes: str | None = Field(default=None, max_length=2000)

    _doc = field_validator("document")(classmethod(lambda cls, v: _digits_doc(v)))

    @field_validator("state")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else None


class CustomerUpdate(BaseModel):
    name: Name | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    notes: str | None = Field(default=None, max_length=2000)
    reason: str = Field(min_length=3, max_length=500)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    document: str | None
    email: str | None
    phone: str | None
    city: str | None
    state: str | None
    notes: str | None
    created_at: datetime


class BrokerIn(BaseModel):
    name: Name
    creci: str | None = Field(default=None, max_length=30)
    document: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)

    _doc = field_validator("document")(classmethod(lambda cls, v: _digits_doc(v)))


class BrokerUpdate(BaseModel):
    name: Name | None = None
    creci: str | None = Field(default=None, max_length=30)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    is_active: bool | None = None
    reason: str = Field(min_length=3, max_length=500)


class BrokerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    creci: str | None
    email: str | None
    phone: str | None
    is_active: bool


class ReservationIn(BaseModel):
    lot_id: uuid.UUID
    customer_id: uuid.UUID
    broker_id: uuid.UUID | None = None
    hours: int | None = Field(default=None, ge=1)
    notes: str | None = Field(default=None, max_length=2000)


class ReservationClose(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ReservationOut(BaseModel):
    id: uuid.UUID
    status: str
    price: Decimal
    expires_at: datetime
    created_at: datetime
    closed_at: datetime | None
    close_reason: str | None
    notes: str | None
    lot_id: uuid.UUID
    lot_number: str
    block_code: str
    development_id: uuid.UUID
    development_name: str
    customer_id: uuid.UUID
    customer_name: str
    broker_id: uuid.UUID | None
    broker_name: str | None


# ─────────────────────────── helpers ───────────────────────────


def _404(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} não encontrado")


def _reservation_query(tenant_id: uuid.UUID) -> Any:
    return (
        select(
            Reservation, Lot.number, Block.code, Development.id, Development.name, Customer.name, Broker.name
        )
        .join(Lot, Lot.id == Reservation.lot_id)
        .join(Block, Block.id == Lot.block_id)
        .join(Development, Development.id == Lot.development_id)
        .join(Customer, Customer.id == Reservation.customer_id)
        .outerjoin(Broker, Broker.id == Reservation.broker_id)
        .where(Reservation.tenant_id == tenant_id)
    )


def _res_out(row: Any) -> ReservationOut:
    r, lot_number, block_code, dev_id, dev_name, customer_name, broker_name = row
    return ReservationOut(
        id=r.id,
        status=r.status,
        price=r.price,
        expires_at=r.expires_at,
        created_at=r.created_at,
        closed_at=r.closed_at,
        close_reason=r.close_reason,
        notes=r.notes,
        lot_id=r.lot_id,
        lot_number=lot_number,
        block_code=block_code,
        development_id=dev_id,
        development_name=dev_name,
        customer_id=r.customer_id,
        customer_name=customer_name,
        broker_id=r.broker_id,
        broker_name=broker_name,
    )


def _get_res_out(ctx: AuthContext, res_id: uuid.UUID) -> ReservationOut:
    row = ctx.db.execute(_reservation_query(ctx.tenant_id).where(Reservation.id == res_id)).one_or_none()
    if row is None:
        raise _404("Reserva")
    return _res_out(row)


def _business(e: svc.ReservationError) -> HTTPException:
    if isinstance(e, svc.NotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=409, detail=str(e))


# ─────────────────────────── compradores ───────────────────────────


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    ctx: AuthContext = Depends(require(Perm.CUSTOMERS_VIEW)),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[Customer]:
    stmt = select(Customer).where(Customer.tenant_id == ctx.tenant_id)
    if q:
        like = f"%{q.lower()}%"
        digits = re.sub(r"\D", "", q)
        conds = [func.lower(Customer.name).like(like), func.lower(Customer.email).like(like)]
        if digits:
            conds.append(Customer.document.like(f"%{digits}%"))
        stmt = stmt.where(or_(*conds))
    return list(ctx.db.scalars(stmt.order_by(Customer.name).limit(limit)).all())


@router.post("/customers", response_model=CustomerOut, status_code=201)
def create_customer(body: CustomerIn, ctx: AuthContext = Depends(require(Perm.CUSTOMERS_MANAGE))) -> Customer:
    if body.document and ctx.db.scalar(
        select(Customer.id).where(Customer.tenant_id == ctx.tenant_id, Customer.document == body.document)
    ):
        raise HTTPException(status_code=409, detail="Já existe comprador com este CPF/CNPJ")
    c = Customer(tenant_id=ctx.tenant_id, **body.model_dump())
    ctx.db.add(c)
    ctx.db.flush()
    ctx.db.refresh(c)
    # Auditoria sem dados pessoais além do nome.
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="customer.create",
        entity="customer",
        entity_id=c.id,
        data={"name": c.name},
    )
    return c


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.CUSTOMERS_VIEW))
) -> Customer:
    c = ctx.db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == ctx.tenant_id))
    if c is None:
        raise _404("Comprador")
    return c


@router.patch("/customers/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: uuid.UUID, body: CustomerUpdate, ctx: AuthContext = Depends(require(Perm.CUSTOMERS_MANAGE))
) -> Customer:
    c = ctx.db.scalar(select(Customer).where(Customer.id == customer_id, Customer.tenant_id == ctx.tenant_id))
    if c is None:
        raise _404("Comprador")
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    for k, v in changes.items():
        setattr(c, k, v.upper() if k == "state" and v else v)
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="customer.update",
        entity="customer",
        entity_id=c.id,
        reason=body.reason,
        data={"fields": sorted(changes)},
    )
    return c


# ─────────────────────────── corretores ───────────────────────────


@router.get("/brokers", response_model=list[BrokerOut])
def list_brokers(
    ctx: AuthContext = Depends(require(Perm.BROKERS_VIEW)), active: bool | None = None
) -> list[Broker]:
    stmt = select(Broker).where(Broker.tenant_id == ctx.tenant_id)
    if active is not None:
        stmt = stmt.where(Broker.is_active.is_(active))
    return list(ctx.db.scalars(stmt.order_by(Broker.name)).all())


@router.post("/brokers", response_model=BrokerOut, status_code=201)
def create_broker(body: BrokerIn, ctx: AuthContext = Depends(require(Perm.BROKERS_MANAGE))) -> Broker:
    b = Broker(tenant_id=ctx.tenant_id, **body.model_dump())
    ctx.db.add(b)
    ctx.db.flush()
    ctx.db.refresh(b)
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="broker.create",
        entity="broker",
        entity_id=b.id,
        data={"name": b.name},
    )
    return b


@router.patch("/brokers/{broker_id}", response_model=BrokerOut)
def update_broker(
    broker_id: uuid.UUID, body: BrokerUpdate, ctx: AuthContext = Depends(require(Perm.BROKERS_MANAGE))
) -> Broker:
    b = ctx.db.scalar(select(Broker).where(Broker.id == broker_id, Broker.tenant_id == ctx.tenant_id))
    if b is None:
        raise _404("Corretor")
    changes = body.model_dump(exclude_unset=True, exclude={"reason"})
    for k, v in changes.items():
        setattr(b, k, v)
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="broker.update",
        entity="broker",
        entity_id=b.id,
        reason=body.reason,
        data={"fields": sorted(changes)},
    )
    return b


# ─────────────────────────── reservas ───────────────────────────


@router.get("/reservations", response_model=list[ReservationOut])
def list_reservations(
    ctx: AuthContext = Depends(require(Perm.RESERVATIONS_VIEW)),
    status_: Literal["ativa", "convertida", "cancelada", "expirada"] | None = Query(
        default=None, alias="status"
    ),
    development_id: uuid.UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[ReservationOut]:
    svc.expire_due(ctx.db, ctx.tenant_id)
    stmt = _reservation_query(ctx.tenant_id)
    if status_:
        stmt = stmt.where(Reservation.status == status_)
    if development_id:
        stmt = stmt.where(Lot.development_id == development_id)
    rows = ctx.db.execute(stmt.order_by(Reservation.created_at.desc()).limit(limit)).all()
    return [_res_out(r) for r in rows]


@router.post("/reservations", response_model=ReservationOut, status_code=201)
def create_reservation(
    body: ReservationIn, ctx: AuthContext = Depends(require(Perm.RESERVATIONS_MANAGE))
) -> ReservationOut:
    s = get_settings()
    hours = body.hours or s.reservation_default_hours
    if hours > s.reservation_max_hours:
        raise HTTPException(status_code=422, detail=f"Validade máxima: {s.reservation_max_hours} horas")
    try:
        res = svc.create(
            ctx.db,
            tenant_id=ctx.tenant_id,
            lot_id=body.lot_id,
            customer_id=body.customer_id,
            broker_id=body.broker_id,
            hours=hours,
            actor=ctx.user.id,
            notes=body.notes,
        )
    except svc.ReservationError as e:
        raise _business(e) from None
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Lote já possui reserva ativa") from None
    return _get_res_out(ctx, res.id)


@router.get("/reservations/{res_id}", response_model=ReservationOut)
def get_reservation(
    res_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.RESERVATIONS_VIEW))
) -> ReservationOut:
    out = _get_res_out(ctx, res_id)
    if out.status == "ativa":
        svc.expire_due(ctx.db, ctx.tenant_id, lot_id=out.lot_id)
        out = _get_res_out(ctx, res_id)
    return out


@router.post("/reservations/{res_id}/cancel", response_model=ReservationOut)
def cancel_reservation(
    res_id: uuid.UUID, body: ReservationClose, ctx: AuthContext = Depends(require(Perm.RESERVATIONS_MANAGE))
) -> ReservationOut:
    try:
        svc.cancel(ctx.db, tenant_id=ctx.tenant_id, res_id=res_id, actor=ctx.user.id, reason=body.reason)
    except svc.ReservationError as e:
        raise _business(e) from None
    return _get_res_out(ctx, res_id)
