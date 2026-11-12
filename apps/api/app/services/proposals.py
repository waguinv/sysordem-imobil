"""Propostas de compra (Fase 2c).

Fluxo:
  aberta ──aceitar──▶ aceita    (reserva o lote para o cliente, ou usa a reserva ativa dele no lote)
  aberta ──recusar──▶ recusada  (cliente ou empresa não aceitaram as condições)
  aberta ──cancelar─▶ cancelada (proposta retirada)
  aberta ──validade vencida──▶ expirada
As condições são validadas pela política de contratos da empresa já na criação e servem de base
para o contrato gerado a partir da reserva.

Travas: lote → proposta → reserva (mesma ordem de reservas e contratos), evitando deadlock.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.models import Lead, LeadActivity, Lot, Proposal, Reservation
from app.services import audit
from app.services import reservations as reservation_svc
from app.services.contracts import ContractError, Terms, evaluate_terms
from app.services.finance import money
from app.services.lots import LotStatus

OPEN = "aberta"


class ProposalError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class ProposalNotFoundError(ProposalError):
    pass


def _today() -> date:
    return business_today()


def _lead_activity(db: Session, p: Proposal, text: str, actor: uuid.UUID | None) -> None:
    if p.lead_id is not None:
        db.add(
            LeadActivity(
                tenant_id=p.tenant_id, lead_id=p.lead_id, kind="status", description=text, created_by=actor
            )
        )


def expire_due(db: Session, tenant_id: uuid.UUID) -> int:
    """Encerra propostas abertas com validade vencida. Idempotente."""
    result = db.execute(
        update(Proposal)
        .where(Proposal.tenant_id == tenant_id, Proposal.status == OPEN, Proposal.valid_until < _today())
        .values(status="expirada", decided_at=datetime.now(UTC), decision_reason="validade encerrada")
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


def create(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    lot_id: uuid.UUID,
    customer_id: uuid.UUID,
    broker_id: uuid.UUID | None,
    lead_id: uuid.UUID | None,
    terms: Terms,
    valid_until: date,
    actor: uuid.UUID,
) -> Proposal:
    lot = db.scalar(select(Lot).where(Lot.id == lot_id, Lot.tenant_id == tenant_id))
    if lot is None:
        raise ProposalNotFoundError("Lote não encontrado")
    reservation_svc.expire_due(db, tenant_id, lot_id=lot.id)
    if lot.status == LotStatus.RESERVADO:
        holder = db.scalar(
            select(Reservation.customer_id).where(
                Reservation.tenant_id == tenant_id,
                Reservation.lot_id == lot.id,
                Reservation.status == "ativa",
            )
        )
        if holder != customer_id:
            raise ProposalError("Lote reservado para outro cliente")
    elif lot.status != LotStatus.DISPONIVEL:
        raise ProposalError(f"Lote não está à venda (situação atual: {lot.status})")

    if lead_id is not None:
        lead = db.scalar(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
        if lead is None:
            raise ProposalNotFoundError("Lead não encontrado")
        if lead.customer_id != customer_id:
            raise ProposalError("O lead informado não foi convertido neste comprador")

    total = terms.total_price if terms.total_price is not None else lot.price
    try:
        r = evaluate_terms(db, tenant_id, terms, total)
    except ContractError as e:
        raise ProposalError(str(e)) from None

    p = Proposal(
        tenant_id=tenant_id,
        lot_id=lot.id,
        customer_id=customer_id,
        broker_id=broker_id,
        lead_id=lead_id,
        status=OPEN,
        valid_until=valid_until,
        total_price=money(total),
        down_payment=money(terms.down_payment),
        installments_count=terms.installments_count,
        installment_amount=r.installment_amount,
        amortization=r.amortization,
        interest_monthly_pct=r.interest_monthly_pct,
        first_due_date=terms.first_due_date if terms.installments_count else None,
        notes=terms.notes,
        created_by=actor,
    )
    db.add(p)
    db.flush()
    _lead_activity(db, p, f"Proposta registrada: {money(total)} (lote {lot.number})", actor)
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="proposal.create",
        entity="proposal",
        entity_id=p.id,
        data={"lot_id": str(lot.id), "total": str(p.total_price), "list_price": str(lot.price)},
    )
    return p


def _lock_open(db: Session, tenant_id: uuid.UUID, proposal_id: uuid.UUID) -> tuple[Proposal, Lot]:
    p = db.scalar(select(Proposal).where(Proposal.id == proposal_id, Proposal.tenant_id == tenant_id))
    if p is None:
        raise ProposalNotFoundError("Proposta não encontrada")
    lot = db.scalar(select(Lot).where(Lot.id == p.lot_id, Lot.tenant_id == tenant_id).with_for_update())
    assert lot is not None
    db.refresh(p, with_for_update=True)
    if p.status == OPEN and p.valid_until < _today():
        expire_due(db, tenant_id)
        db.refresh(p)
    if p.status != OPEN:
        raise ProposalError(f"Proposta não está aberta (situação: {p.status})")
    return p, lot


def _decide(p: Proposal, status: str, actor: uuid.UUID, reason: str) -> None:
    p.status = status
    p.decided_at = datetime.now(UTC)
    p.decided_by = actor
    p.decision_reason = reason


def accept(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    proposal_id: uuid.UUID,
    hours: int,
    actor: uuid.UUID,
    reason: str,
) -> Proposal:
    """Aceita a proposta e garante a reserva do lote para o cliente."""
    p, lot = _lock_open(db, tenant_id, proposal_id)
    reservation_svc.expire_due(db, tenant_id, lot_id=lot.id)
    active = db.scalar(
        select(Reservation).where(
            Reservation.tenant_id == tenant_id, Reservation.lot_id == lot.id, Reservation.status == "ativa"
        )
    )
    if active is not None:
        if active.customer_id != p.customer_id:
            raise ProposalError("Lote reservado para outro cliente")
        linked = db.scalar(select(Proposal.id).where(Proposal.reservation_id == active.id))
        if linked is not None:
            raise ProposalError("A reserva deste cliente já está ligada a outra proposta aceita")
        res = active
    elif lot.status == LotStatus.DISPONIVEL:
        try:
            res = reservation_svc.create(
                db,
                tenant_id=tenant_id,
                lot_id=lot.id,
                customer_id=p.customer_id,
                broker_id=p.broker_id,
                hours=hours,
                actor=actor,
                notes=f"proposta aceita ({money(p.total_price)})",
                price=p.total_price,
            )
        except reservation_svc.ReservationError as e:
            raise ProposalError(str(e)) from None
    else:
        raise ProposalError(f"Lote não está à venda (situação atual: {lot.status})")

    _decide(p, "aceita", actor, reason)
    p.reservation_id = res.id
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise ProposalError("A reserva deste cliente já está ligada a outra proposta aceita") from None
    _lead_activity(db, p, "Proposta aceita — lote reservado", actor)
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="proposal.accept",
        entity="proposal",
        entity_id=p.id,
        reason=reason,
        data={"reservation_id": str(res.id)},
    )
    return p


def close(
    db: Session, *, tenant_id: uuid.UUID, proposal_id: uuid.UUID, status: str, actor: uuid.UUID, reason: str
) -> Proposal:
    if status not in ("recusada", "cancelada"):
        raise ValueError(status)
    p, _lot = _lock_open(db, tenant_id, proposal_id)
    _decide(p, status, actor, reason)
    db.flush()
    _lead_activity(db, p, f"Proposta {status}: {reason}", actor)
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action=f"proposal.{'reject' if status == 'recusada' else 'cancel'}",
        entity="proposal",
        entity_id=p.id,
        reason=reason,
    )
    return p


def default_valid_until(days: int) -> date:
    return _today() + timedelta(days=days)


def discount_pct(total: Decimal, list_price: Decimal) -> Decimal:
    """Desconto sobre o preço de tabela (negativo = ágio)."""
    if list_price <= 0:
        return Decimal("0")
    return money((list_price - total) * 100 / list_price)
