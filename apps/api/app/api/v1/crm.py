"""Fase 2c: CRM (leads e atividades) e propostas, com escopo por corretor."""

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm, can
from app.auth.scope import Scope, check_customer, resolve_broker, scope_for
from app.core.config import get_settings
from app.models import Block, Broker, Customer, Development, Lead, LeadActivity, Lot, Proposal, User
from app.services import audit
from app.services import proposals as svc
from app.services.contracts import Terms

router = APIRouter(tags=["crm"])

Name = Annotated[str, Field(min_length=2, max_length=160)]
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Pct = Annotated[Decimal, Field(ge=0, le=10, max_digits=7, decimal_places=4)]
Source = Literal["site", "indicacao", "whatsapp", "telefone", "visita", "redes_sociais", "outro"]
OpenStatus = Literal["novo", "em_contato", "qualificado", "perdido"]
LeadStatus = Literal["novo", "em_contato", "qualificado", "convertido", "perdido"]
ActivityKind = Literal["nota", "ligacao", "whatsapp", "email", "visita"]
ProposalStatus = Literal["aberta", "aceita", "recusada", "cancelada", "expirada"]

STATUS_LABEL = {
    "novo": "Novo",
    "em_contato": "Em contato",
    "qualificado": "Qualificado",
    "convertido": "Convertido",
    "perdido": "Perdido",
}


def _digits_doc(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    d = re.sub(r"\D", "", v)
    if len(d) not in (11, 14):
        raise ValueError("CPF (11 dígitos) ou CNPJ (14 dígitos)")
    return d


def _404(what: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} não encontrado")


# ─────────────────────────── DTOs: leads ───────────────────────────


class LeadIn(BaseModel):
    name: Name
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    source: Source = "outro"
    development_id: uuid.UUID | None = None
    broker_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _contact(self) -> "LeadIn":
        if not self.email and not (self.phone and self.phone.strip()):
            raise ValueError("Informe telefone ou e-mail para contato")
        return self


class LeadUpdate(BaseModel):
    name: Name | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    source: Source | None = None
    development_id: uuid.UUID | None = None
    broker_id: uuid.UUID | None = None
    notes: str | None = Field(default=None, max_length=4000)
    status: OpenStatus | None = None
    lost_reason: str | None = Field(default=None, min_length=3, max_length=500)


class LeadOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None
    phone: str | None
    source: str
    status: str
    development_id: uuid.UUID | None
    development_name: str | None
    broker_id: uuid.UUID | None
    broker_name: str | None
    customer_id: uuid.UUID | None
    lost_reason: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ActivityIn(BaseModel):
    kind: ActivityKind = "nota"
    description: str = Field(min_length=2, max_length=4000)


class ActivityOut(BaseModel):
    id: uuid.UUID
    kind: str
    description: str
    author_name: str | None
    created_at: datetime


class LeadDetailOut(LeadOut):
    activities: list[ActivityOut]


class ConvertIn(BaseModel):
    """Converte o lead em comprador: liga a um comprador existente ou cria um novo com os dados do lead."""

    customer_id: uuid.UUID | None = None
    document: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, min_length=2, max_length=2)

    _doc = field_validator("document")(classmethod(lambda cls, v: _digits_doc(v)))


# ─────────────────────────── DTOs: propostas ───────────────────────────


class ProposalIn(BaseModel):
    lot_id: uuid.UUID
    customer_id: uuid.UUID
    broker_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    valid_until: date | None = None  # padrão: hoje + PROPOSAL_DEFAULT_DAYS
    total_price: Money | None = None  # padrão: preço de tabela do lote
    down_payment: Money
    installments_count: int = Field(ge=0, le=480)
    interest_monthly_pct: Pct | None = None  # padrão: política da empresa
    amortization: Literal["price", "sac"] | None = None
    first_due_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class AcceptIn(BaseModel):
    hours: int | None = Field(default=None, ge=1)  # validade da reserva criada ao aceitar
    reason: str = Field(default="proposta aceita", min_length=3, max_length=500)


class CloseIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class ProposalOut(BaseModel):
    id: uuid.UUID
    status: str
    valid_until: date
    total_price: Decimal
    list_price: Decimal
    discount_pct: Decimal
    down_payment: Decimal
    installments_count: int
    installment_amount: Decimal
    amortization: str
    interest_monthly_pct: Decimal
    first_due_date: date | None
    notes: str | None
    lot_id: uuid.UUID
    lot_number: str
    lot_status: str
    block_code: str
    development_id: uuid.UUID
    development_name: str
    customer_id: uuid.UUID
    customer_name: str
    broker_id: uuid.UUID | None
    broker_name: str | None
    lead_id: uuid.UUID | None
    reservation_id: uuid.UUID | None
    created_at: datetime
    decided_at: datetime | None
    decision_reason: str | None


# ─────────────────────────── helpers ───────────────────────────


def _check_broker(ctx: AuthContext, broker_id: uuid.UUID | None, *, active: bool = True) -> None:
    if broker_id is None:
        return
    b = ctx.db.scalar(select(Broker).where(Broker.id == broker_id, Broker.tenant_id == ctx.tenant_id))
    if b is None:
        raise _404("Corretor")
    if active and not b.is_active:
        raise HTTPException(status_code=409, detail="Corretor inativo")


def _check_development(ctx: AuthContext, development_id: uuid.UUID | None) -> None:
    if (
        development_id is not None
        and ctx.db.scalar(
            select(Development.id).where(
                Development.id == development_id, Development.tenant_id == ctx.tenant_id
            )
        )
        is None
    ):
        raise _404("Empreendimento")


def _lead_query(ctx: AuthContext, scope: Scope) -> Any:
    stmt = (
        select(Lead, Development.name, Broker.name)
        .outerjoin(Development, Development.id == Lead.development_id)
        .outerjoin(Broker, Broker.id == Lead.broker_id)
        .where(Lead.tenant_id == ctx.tenant_id)
    )
    if scope.restricted:
        stmt = stmt.where(Lead.broker_id == scope.broker_id)
    return stmt


def _lead_out(row: Any) -> LeadOut:
    lead, dev_name, broker_name = row
    return LeadOut(
        id=lead.id,
        name=lead.name,
        email=lead.email,
        phone=lead.phone,
        source=lead.source,
        status=lead.status,
        development_id=lead.development_id,
        development_name=dev_name,
        broker_id=lead.broker_id,
        broker_name=broker_name,
        customer_id=lead.customer_id,
        lost_reason=lead.lost_reason,
        notes=lead.notes,
        created_at=lead.created_at,
        updated_at=lead.updated_at,
    )


def _get_lead(ctx: AuthContext, scope: Scope, lead_id: uuid.UUID, *, lock: bool = False) -> Lead:
    stmt = select(Lead).where(Lead.id == lead_id, Lead.tenant_id == ctx.tenant_id)
    if scope.restricted:
        stmt = stmt.where(Lead.broker_id == scope.broker_id)
    if lock:
        stmt = stmt.with_for_update()
    lead = ctx.db.scalar(stmt)
    if lead is None:
        raise _404("Lead")
    return lead


def _lead_detail(ctx: AuthContext, scope: Scope, lead_id: uuid.UUID) -> LeadDetailOut:
    row = ctx.db.execute(_lead_query(ctx, scope).where(Lead.id == lead_id)).one_or_none()
    if row is None:
        raise _404("Lead")
    acts = ctx.db.execute(
        select(LeadActivity, User.full_name)
        .outerjoin(User, User.id == LeadActivity.created_by)
        .where(LeadActivity.lead_id == lead_id, LeadActivity.tenant_id == ctx.tenant_id)
        .order_by(LeadActivity.seq.desc())
    ).all()
    return LeadDetailOut(
        **_lead_out(row).model_dump(),
        activities=[
            ActivityOut(
                id=a.id, kind=a.kind, description=a.description, author_name=author, created_at=a.created_at
            )
            for a, author in acts
        ],
    )


def _activity(ctx: AuthContext, lead: Lead, kind: str, text: str) -> None:
    ctx.db.add(
        LeadActivity(
            tenant_id=ctx.tenant_id, lead_id=lead.id, kind=kind, description=text, created_by=ctx.user.id
        )
    )


# ─────────────────────────── leads ───────────────────────────


@router.get("/leads", response_model=list[LeadOut])
def list_leads(
    ctx: AuthContext = Depends(require(Perm.LEADS_VIEW)),
    status_: LeadStatus | None = Query(default=None, alias="status"),
    broker_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[LeadOut]:
    stmt = _lead_query(ctx, scope_for(ctx))
    if status_:
        stmt = stmt.where(Lead.status == status_)
    if broker_id:
        stmt = stmt.where(Lead.broker_id == broker_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(func.lower(Lead.name).like(like), func.lower(Lead.email).like(like), Lead.phone.like(like))
        )
    rows = ctx.db.execute(stmt.order_by(Lead.updated_at.desc()).limit(limit)).all()
    return [_lead_out(r) for r in rows]


@router.post("/leads", response_model=LeadDetailOut, status_code=201)
def create_lead(body: LeadIn, ctx: AuthContext = Depends(require(Perm.LEADS_MANAGE))) -> LeadDetailOut:
    scope = scope_for(ctx)
    broker_id = resolve_broker(scope, body.broker_id)
    _check_broker(ctx, broker_id)
    _check_development(ctx, body.development_id)
    lead = Lead(
        tenant_id=ctx.tenant_id,
        **body.model_dump(exclude={"broker_id"}),
        broker_id=broker_id,
        status="novo",
        created_by=ctx.user.id,
    )
    ctx.db.add(lead)
    ctx.db.flush()
    _activity(ctx, lead, "status", "Lead cadastrado")
    # Auditoria sem dados de contato (minimização LGPD).
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="lead.create",
        entity="lead",
        entity_id=lead.id,
        data={"source": lead.source, "broker_id": str(broker_id) if broker_id else None},
    )
    ctx.db.flush()
    return _lead_detail(ctx, scope, lead.id)


@router.get("/leads/{lead_id}", response_model=LeadDetailOut)
def get_lead(lead_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.LEADS_VIEW))) -> LeadDetailOut:
    return _lead_detail(ctx, scope_for(ctx), lead_id)


@router.patch("/leads/{lead_id}", response_model=LeadDetailOut)
def update_lead(
    lead_id: uuid.UUID, body: LeadUpdate, ctx: AuthContext = Depends(require(Perm.LEADS_MANAGE))
) -> LeadDetailOut:
    scope = scope_for(ctx)
    lead = _get_lead(ctx, scope, lead_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if lead.status == "convertido" and changes.keys() - {"notes"}:
        raise HTTPException(
            status_code=409, detail="Lead convertido: altere os dados no cadastro do comprador"
        )
    if "broker_id" in changes:
        if scope.restricted:
            raise HTTPException(status_code=403, detail="Só a gestão comercial redistribui leads")
        _check_broker(ctx, body.broker_id)
    if "development_id" in changes:
        _check_development(ctx, body.development_id)

    new_status = changes.pop("status", None)
    lost_reason = changes.pop("lost_reason", None)
    for k, v in changes.items():
        setattr(lead, k, v)
    if new_status and new_status != lead.status:
        if new_status == "perdido" and not lost_reason:
            raise HTTPException(status_code=422, detail="Informe o motivo da perda")
        old = lead.status
        lead.status = new_status
        lead.lost_reason = lost_reason if new_status == "perdido" else None
        text = f"{STATUS_LABEL[old]} → {STATUS_LABEL[new_status]}"
        _activity(ctx, lead, "status", f"{text}: {lost_reason}" if lost_reason else text)
    if "broker_id" in changes:
        _activity(ctx, lead, "status", "Lead redistribuído para outro corretor")
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="lead.update",
        entity="lead",
        entity_id=lead.id,
        data={"fields": sorted(body.model_fields_set), "status": lead.status},
    )
    return _lead_detail(ctx, scope, lead.id)


@router.post("/leads/{lead_id}/activities", response_model=LeadDetailOut, status_code=201)
def add_activity(
    lead_id: uuid.UUID, body: ActivityIn, ctx: AuthContext = Depends(require(Perm.LEADS_MANAGE))
) -> LeadDetailOut:
    scope = scope_for(ctx)
    lead = _get_lead(ctx, scope, lead_id, lock=True)
    _activity(ctx, lead, body.kind, body.description)
    if lead.status == "novo" and body.kind != "nota":
        lead.status = "em_contato"
        _activity(ctx, lead, "status", "Novo → Em contato")
    lead.updated_at = datetime.now(UTC)
    ctx.db.flush()
    return _lead_detail(ctx, scope, lead.id)


@router.post("/leads/{lead_id}/convert", response_model=LeadDetailOut)
def convert_lead(
    lead_id: uuid.UUID, body: ConvertIn, ctx: AuthContext = Depends(require(Perm.LEADS_MANAGE))
) -> LeadDetailOut:
    """Converte o lead em comprador (exige também permissão de cadastrar compradores)."""
    if not can(ctx.role, Perm.CUSTOMERS_MANAGE):
        raise HTTPException(status_code=403, detail="Sem permissão")
    scope = scope_for(ctx)
    lead = _get_lead(ctx, scope, lead_id, lock=True)
    if lead.status == "convertido":
        raise HTTPException(status_code=409, detail="Lead já convertido")
    if lead.status == "perdido":
        raise HTTPException(status_code=409, detail="Lead perdido: reabra antes de converter")

    if body.customer_id is not None:
        check_customer(ctx, scope, body.customer_id)
        customer_id = body.customer_id
        text = "Convertido: ligado a comprador já cadastrado"
    else:
        if body.document and ctx.db.scalar(
            select(Customer.id).where(Customer.tenant_id == ctx.tenant_id, Customer.document == body.document)
        ):
            raise HTTPException(
                status_code=409, detail="Já existe comprador com este CPF/CNPJ — ligue o lead a ele"
            )
        c = Customer(
            tenant_id=ctx.tenant_id,
            name=lead.name,
            email=lead.email,
            phone=lead.phone,
            document=body.document,
            city=body.city,
            state=body.state.upper() if body.state else None,
            broker_id=lead.broker_id,
        )
        ctx.db.add(c)
        ctx.db.flush()
        customer_id = c.id
        text = "Convertido: comprador cadastrado"
        audit.record(
            ctx.db,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user.id,
            action="customer.create",
            entity="customer",
            entity_id=c.id,
            data={"name": c.name, "lead_id": str(lead.id)},
        )
    lead.status = "convertido"
    lead.customer_id = customer_id
    lead.lost_reason = None
    _activity(ctx, lead, "status", text)
    ctx.db.flush()
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="lead.convert",
        entity="lead",
        entity_id=lead.id,
        data={"customer_id": str(customer_id)},
    )
    return _lead_detail(ctx, scope, lead.id)


# ─────────────────────────── propostas ───────────────────────────


def _proposal_query(ctx: AuthContext, scope: Scope) -> Any:
    b = aliased(Broker)
    stmt = (
        select(Proposal, Lot.number, Lot.status, Lot.price, Block.code, Development.id, Development.name)
        .add_columns(Customer.name, b.name)
        .join(Lot, Lot.id == Proposal.lot_id)
        .join(Block, Block.id == Lot.block_id)
        .join(Development, Development.id == Lot.development_id)
        .join(Customer, Customer.id == Proposal.customer_id)
        .outerjoin(b, b.id == Proposal.broker_id)
        .where(Proposal.tenant_id == ctx.tenant_id)
    )
    if scope.restricted:
        stmt = stmt.where(Proposal.broker_id == scope.broker_id)
    return stmt


def _proposal_out(row: Any) -> ProposalOut:
    p, lot_number, lot_status, list_price, block_code, dev_id, dev_name, customer_name, broker_name = row
    return ProposalOut(
        id=p.id,
        status=p.status,
        valid_until=p.valid_until,
        total_price=p.total_price,
        list_price=list_price,
        discount_pct=svc.discount_pct(p.total_price, list_price),
        down_payment=p.down_payment,
        installments_count=p.installments_count,
        installment_amount=p.installment_amount,
        amortization=p.amortization,
        interest_monthly_pct=p.interest_monthly_pct,
        first_due_date=p.first_due_date,
        notes=p.notes,
        lot_id=p.lot_id,
        lot_number=lot_number,
        lot_status=lot_status,
        block_code=block_code,
        development_id=dev_id,
        development_name=dev_name,
        customer_id=p.customer_id,
        customer_name=customer_name,
        broker_id=p.broker_id,
        broker_name=broker_name,
        lead_id=p.lead_id,
        reservation_id=p.reservation_id,
        created_at=p.created_at,
        decided_at=p.decided_at,
        decision_reason=p.decision_reason,
    )


def _get_proposal_out(ctx: AuthContext, scope: Scope, proposal_id: uuid.UUID) -> ProposalOut:
    row = ctx.db.execute(_proposal_query(ctx, scope).where(Proposal.id == proposal_id)).one_or_none()
    if row is None:
        raise _404("Proposta")
    return _proposal_out(row)


def _business(e: svc.ProposalError) -> HTTPException:
    if isinstance(e, svc.ProposalNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    return HTTPException(status_code=409, detail=str(e))


@router.get("/proposals", response_model=list[ProposalOut])
def list_proposals(
    ctx: AuthContext = Depends(require(Perm.PROPOSALS_VIEW)),
    status_: ProposalStatus | None = Query(default=None, alias="status"),
    lot_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    reservation_id: uuid.UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[ProposalOut]:
    svc.expire_due(ctx.db, ctx.tenant_id)
    stmt = _proposal_query(ctx, scope_for(ctx))
    for col, val in (
        (Proposal.status, status_),
        (Proposal.lot_id, lot_id),
        (Proposal.customer_id, customer_id),
        (Proposal.lead_id, lead_id),
        (Proposal.reservation_id, reservation_id),
    ):
        if val is not None:
            stmt = stmt.where(col == val)
    rows = ctx.db.execute(stmt.order_by(Proposal.created_at.desc()).limit(limit)).all()
    return [_proposal_out(r) for r in rows]


@router.post("/proposals", response_model=ProposalOut, status_code=201)
def create_proposal(
    body: ProposalIn, ctx: AuthContext = Depends(require(Perm.PROPOSALS_MANAGE))
) -> ProposalOut:
    s = get_settings()
    scope = scope_for(ctx)
    broker_id = resolve_broker(scope, body.broker_id)
    _check_broker(ctx, broker_id)
    check_customer(ctx, scope, body.customer_id)
    if body.lead_id is not None:
        _get_lead(ctx, scope, body.lead_id)
    today = svc.default_valid_until(0)
    valid_until = body.valid_until or svc.default_valid_until(s.proposal_default_days)
    if valid_until < today:
        raise HTTPException(status_code=422, detail="Validade da proposta não pode estar no passado")
    if valid_until > svc.default_valid_until(s.proposal_max_days):
        raise HTTPException(status_code=422, detail=f"Validade máxima: {s.proposal_max_days} dias")
    terms = Terms(
        total_price=body.total_price,
        down_payment=body.down_payment,
        installments_count=body.installments_count,
        interest_monthly_pct=body.interest_monthly_pct,
        first_due_date=body.first_due_date,
        adjustment_index=None,
        late_fee_pct=None,
        late_interest_monthly_pct=None,
        notes=body.notes,
        amortization=body.amortization,
    )
    try:
        p = svc.create(
            ctx.db,
            tenant_id=ctx.tenant_id,
            lot_id=body.lot_id,
            customer_id=body.customer_id,
            broker_id=broker_id,
            lead_id=body.lead_id,
            terms=terms,
            valid_until=valid_until,
            actor=ctx.user.id,
        )
    except svc.ProposalError as e:
        raise _business(e) from None
    return _get_proposal_out(ctx, scope, p.id)


@router.get("/proposals/{proposal_id}", response_model=ProposalOut)
def get_proposal(
    proposal_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.PROPOSALS_VIEW))
) -> ProposalOut:
    svc.expire_due(ctx.db, ctx.tenant_id)
    return _get_proposal_out(ctx, scope_for(ctx), proposal_id)


@router.post("/proposals/{proposal_id}/accept", response_model=ProposalOut)
def accept_proposal(
    proposal_id: uuid.UUID, body: AcceptIn, ctx: AuthContext = Depends(require(Perm.PROPOSALS_MANAGE))
) -> ProposalOut:
    """Aceita e reserva o lote — exige também permissão de reservar."""
    if not can(ctx.role, Perm.RESERVATIONS_MANAGE):
        raise HTTPException(status_code=403, detail="Sem permissão")
    s = get_settings()
    hours = body.hours or s.reservation_default_hours
    if hours > s.reservation_max_hours:
        raise HTTPException(status_code=422, detail=f"Validade máxima: {s.reservation_max_hours} horas")
    scope = scope_for(ctx)
    _get_proposal_out(ctx, scope, proposal_id)  # 404 fora do escopo
    try:
        svc.accept(
            ctx.db,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            hours=hours,
            actor=ctx.user.id,
            reason=body.reason,
        )
    except svc.ProposalError as e:
        raise _business(e) from None
    return _get_proposal_out(ctx, scope, proposal_id)


def _close(ctx: AuthContext, proposal_id: uuid.UUID, status: str, reason: str) -> ProposalOut:
    scope = scope_for(ctx)
    _get_proposal_out(ctx, scope, proposal_id)
    try:
        svc.close(
            ctx.db,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            status=status,
            actor=ctx.user.id,
            reason=reason,
        )
    except svc.ProposalError as e:
        raise _business(e) from None
    return _get_proposal_out(ctx, scope, proposal_id)


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalOut)
def reject_proposal(
    proposal_id: uuid.UUID, body: CloseIn, ctx: AuthContext = Depends(require(Perm.PROPOSALS_MANAGE))
) -> ProposalOut:
    return _close(ctx, proposal_id, "recusada", body.reason)


@router.post("/proposals/{proposal_id}/cancel", response_model=ProposalOut)
def cancel_proposal(
    proposal_id: uuid.UUID, body: CloseIn, ctx: AuthContext = Depends(require(Perm.PROPOSALS_MANAGE))
) -> ProposalOut:
    return _close(ctx, proposal_id, "cancelada", body.reason)
