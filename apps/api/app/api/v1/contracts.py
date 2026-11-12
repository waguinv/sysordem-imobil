"""Fase 2b: contratos de venda."""

import uuid
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.auth.deps import AuthContext, get_auth, require
from app.auth.rbac import Perm, can
from app.models import Block, Broker, ContractVersion, Customer, Development, Lot, SalesContract
from app.services import contracts as svc
from app.services import policy as policy_svc

router = APIRouter(tags=["contratos"])

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Pct = Annotated[Decimal, Field(ge=0, le=10, max_digits=7, decimal_places=4)]
Index = Literal["nenhum", "IPCA", "IGPM", "INCC"]
Amortization = Literal["price", "sac"]


class TermsIn(BaseModel):
    total_price: Money | None = None  # padrão: preço congelado na reserva / versão anterior
    down_payment: Money
    installments_count: int = Field(ge=0, le=480)
    # Campos opcionais: quando omitidos, valem os padrões da política da empresa.
    interest_monthly_pct: Pct | None = None
    amortization: Amortization | None = None
    first_due_date: date | None = None
    adjustment_index: Index | None = None
    late_fee_pct: Annotated[Decimal, Field(ge=0, le=10, decimal_places=2)] | None = None
    late_interest_monthly_pct: Annotated[Decimal, Field(ge=0, le=10, decimal_places=2)] | None = None
    notes: str | None = Field(default=None, max_length=4000)

    def to_terms(self) -> svc.Terms:
        return svc.Terms(**self.model_dump())


class ContractCreateIn(TermsIn):
    reservation_id: uuid.UUID


class NewVersionIn(TermsIn):
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class SignIn(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(default="assinatura registrada", min_length=3, max_length=500)


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    total_price: Decimal
    down_payment: Decimal
    installments_count: int
    installment_amount: Decimal  # SAC: valor da 1ª parcela (as seguintes diminuem)
    amortization: str
    interest_monthly_pct: Decimal
    first_due_date: date | None
    adjustment_index: str
    late_fee_pct: Decimal
    late_interest_monthly_pct: Decimal
    notes: str | None
    reason: str | None
    created_at: datetime


class ContractOut(BaseModel):
    id: uuid.UUID
    number: str
    status: str
    current_version: int
    created_at: datetime
    signed_at: datetime | None
    cancelled_at: datetime | None
    cancel_reason: str | None
    lot_id: uuid.UUID
    lot_number: str
    block_code: str
    development_id: uuid.UUID
    development_name: str
    customer_id: uuid.UUID
    customer_name: str
    broker_name: str | None
    reservation_id: uuid.UUID | None
    terms: VersionOut


class ContractDetailOut(ContractOut):
    versions: list[VersionOut]


def _query(tenant_id: uuid.UUID) -> Any:
    return (
        select(
            SalesContract,
            ContractVersion,
            Lot.number,
            Block.code,
            Development.id,
            Development.name,
            Customer.name,
            Broker.name,
        )
        .join(
            ContractVersion,
            (ContractVersion.contract_id == SalesContract.id)
            & (ContractVersion.version == SalesContract.current_version),
        )
        .join(Lot, Lot.id == SalesContract.lot_id)
        .join(Block, Block.id == Lot.block_id)
        .join(Development, Development.id == Lot.development_id)
        .join(Customer, Customer.id == SalesContract.customer_id)
        .outerjoin(Broker, Broker.id == SalesContract.broker_id)
        .where(SalesContract.tenant_id == tenant_id)
    )


def _out(row: Any) -> ContractOut:
    c, v, lot_number, block_code, dev_id, dev_name, customer_name, broker_name = row
    return ContractOut(
        id=c.id,
        number=c.number,
        status=c.status,
        current_version=c.current_version,
        created_at=c.created_at,
        signed_at=c.signed_at,
        cancelled_at=c.cancelled_at,
        cancel_reason=c.cancel_reason,
        lot_id=c.lot_id,
        lot_number=lot_number,
        block_code=block_code,
        development_id=dev_id,
        development_name=dev_name,
        customer_id=c.customer_id,
        customer_name=customer_name,
        broker_name=broker_name,
        reservation_id=c.reservation_id,
        terms=VersionOut.model_validate(v),
    )


def _detail(ctx: AuthContext, contract_id: uuid.UUID) -> ContractDetailOut:
    row = ctx.db.execute(_query(ctx.tenant_id).where(SalesContract.id == contract_id)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Contrato não encontrado")
    versions = ctx.db.scalars(
        select(ContractVersion)
        .where(ContractVersion.contract_id == contract_id, ContractVersion.tenant_id == ctx.tenant_id)
        .order_by(ContractVersion.version.desc())
    ).all()
    return ContractDetailOut(
        **_out(row).model_dump(), versions=[VersionOut.model_validate(v) for v in versions]
    )


def _business(e: svc.ContractError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(e, svc.ContractNotFoundError) else 409, detail=str(e))


@router.get("/contracts", response_model=list[ContractOut])
def list_contracts(
    ctx: AuthContext = Depends(require(Perm.CONTRACTS_VIEW)),
    status: Literal["rascunho", "assinado", "cancelado"] | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[ContractOut]:
    stmt = _query(ctx.tenant_id)
    if status:
        stmt = stmt.where(SalesContract.status == status)
    return [
        _out(r) for r in ctx.db.execute(stmt.order_by(SalesContract.created_at.desc()).limit(limit)).all()
    ]


@router.post("/contracts", response_model=ContractDetailOut, status_code=201)
def create_contract(
    body: ContractCreateIn, ctx: AuthContext = Depends(require(Perm.CONTRACTS_MANAGE))
) -> ContractDetailOut:
    terms = TermsIn.model_validate(body.model_dump(exclude={"reservation_id"})).to_terms()
    try:
        c = svc.create_from_reservation(
            ctx.db,
            tenant_id=ctx.tenant_id,
            reservation_id=body.reservation_id,
            terms=terms,
            actor=ctx.user.id,
        )
    except svc.ContractError as e:
        raise _business(e) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return _detail(ctx, c.id)


@router.get("/contracts/{contract_id}", response_model=ContractDetailOut)
def get_contract(
    contract_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.CONTRACTS_VIEW))
) -> ContractDetailOut:
    return _detail(ctx, contract_id)


@router.post("/contracts/{contract_id}/versions", response_model=ContractDetailOut)
def new_version(
    contract_id: uuid.UUID, body: NewVersionIn, ctx: AuthContext = Depends(require(Perm.CONTRACTS_MANAGE))
) -> ContractDetailOut:
    terms = TermsIn.model_validate(body.model_dump(exclude={"expected_version", "reason"})).to_terms()
    try:
        svc.new_version(
            ctx.db,
            tenant_id=ctx.tenant_id,
            contract_id=contract_id,
            terms=terms,
            expected_version=body.expected_version,
            actor=ctx.user.id,
            reason=body.reason,
        )
    except svc.ContractError as e:
        raise _business(e) from None
    return _detail(ctx, contract_id)


@router.post("/contracts/{contract_id}/sign", response_model=ContractDetailOut)
def sign_contract(
    contract_id: uuid.UUID, body: SignIn, ctx: AuthContext = Depends(require(Perm.CONTRACTS_SIGN))
) -> ContractDetailOut:
    try:
        svc.sign(
            ctx.db,
            tenant_id=ctx.tenant_id,
            contract_id=contract_id,
            expected_version=body.expected_version,
            actor=ctx.user.id,
            reason=body.reason,
        )
    except svc.ContractError as e:
        raise _business(e) from None
    return _detail(ctx, contract_id)


@router.post("/contracts/{contract_id}/cancel", response_model=ContractDetailOut)
def cancel_contract(
    contract_id: uuid.UUID, body: CancelIn, ctx: AuthContext = Depends(require(Perm.CONTRACTS_MANAGE))
) -> ContractDetailOut:
    try:
        svc.cancel(
            ctx.db, tenant_id=ctx.tenant_id, contract_id=contract_id, actor=ctx.user.id, reason=body.reason
        )
    except svc.ContractError as e:
        raise _business(e) from None
    return _detail(ctx, contract_id)


# ─────────────────────────── política de contratos da empresa ───────────────────────────


class PolicyOut(BaseModel):
    default_interest_monthly_pct: Decimal
    max_interest_monthly_pct: Decimal
    default_amortization: str
    allowed_amortizations: list[str]
    default_adjustment_index: str
    late_fee_pct: Decimal
    late_interest_monthly_pct: Decimal
    max_installments: int
    min_down_payment_pct: Decimal
    allow_override_late_charges: bool
    is_default: bool


class PolicyIn(BaseModel):
    default_interest_monthly_pct: Pct
    max_interest_monthly_pct: Pct
    default_amortization: Amortization
    allowed_amortizations: list[Amortization] = Field(min_length=1, max_length=2)
    default_adjustment_index: Index
    late_fee_pct: Annotated[Decimal, Field(ge=0, le=10, decimal_places=2)]
    late_interest_monthly_pct: Annotated[Decimal, Field(ge=0, le=10, decimal_places=2)]
    max_installments: int = Field(ge=0, le=480)
    min_down_payment_pct: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)]
    allow_override_late_charges: bool = False
    reason: str = Field(min_length=3, max_length=500)


def _policy_out(p: policy_svc.Policy) -> PolicyOut:
    d = asdict(p)
    d["allowed_amortizations"] = list(p.allowed_amortizations)
    return PolicyOut(**d)


@router.get("/settings/contract-policy", response_model=PolicyOut)
def get_contract_policy(ctx: AuthContext = Depends(get_auth)) -> PolicyOut:
    """Quem vê contratos ou propostas precisa dos padrões e limites (o corretor monta propostas)."""
    if not (can(ctx.role, Perm.CONTRACTS_VIEW) or can(ctx.role, Perm.PROPOSALS_VIEW)):
        raise HTTPException(status_code=403, detail="Sem permissão")
    return _policy_out(policy_svc.get_policy(ctx.db, ctx.tenant_id))


@router.put("/settings/contract-policy", response_model=PolicyOut)
def put_contract_policy(
    body: PolicyIn, ctx: AuthContext = Depends(require(Perm.SETTINGS_MANAGE))
) -> PolicyOut:
    data = body.model_dump(exclude={"reason"})
    data["allowed_amortizations"] = tuple(dict.fromkeys(data["allowed_amortizations"]))
    try:
        saved = policy_svc.save_policy(
            ctx.db,
            ctx.tenant_id,
            policy_svc.Policy(**data, is_default=False),
            actor=ctx.user.id,
            reason=body.reason,
        )
    except policy_svc.PolicyError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return _policy_out(saved)
