"""Reservas de lote com validade.

Garantias contra reserva/venda dupla, em camadas:
1. o lote é travado (`SELECT ... FOR UPDATE`) antes de qualquer verificação;
2. só lote "disponivel" pode ser reservado;
3. índice único parcial `reservations(lot_id) WHERE status='ativa'` no banco.

Expiração: `expire_due` encerra reservas vencidas e libera o lote. É chamada de forma preguiçosa
(antes de reservar, listar reservas e abrir mapa/lote) e pelo job `python -m scripts.expire_reservations`.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Broker, Customer, Lot, Reservation
from app.services import audit
from app.services.lots import LotStatus, apply_status

ACTIVE = "ativa"


class ReservationError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class NotFoundError(ReservationError):
    pass


class LotUnavailableError(ReservationError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _close(
    db: Session,
    res: Reservation,
    lot: Lot,
    *,
    status: str,
    to_lot: LotStatus,
    actor: uuid.UUID | None,
    reason: str,
) -> None:
    res.status = status
    res.closed_at = _now()
    res.close_reason = reason
    if lot.status == LotStatus.RESERVADO:
        apply_status(
            db, lot, to_lot, actor_user_id=actor, reason=reason, extra={"reservation_id": str(res.id)}
        )
    audit.record(
        db,
        tenant_id=res.tenant_id,
        actor_user_id=actor,
        action=f"reservation.{status}",
        entity="reservation",
        entity_id=res.id,
        reason=reason,
    )


def expire_due(
    db: Session, tenant_id: uuid.UUID, *, lot_id: uuid.UUID | None = None, now: datetime | None = None
) -> int:
    """Expira reservas vencidas do tenant (ou de um lote). Idempotente; seguro em paralelo (SKIP LOCKED)."""
    q = select(Reservation).where(
        Reservation.tenant_id == tenant_id,
        Reservation.status == ACTIVE,
        Reservation.expires_at <= (now or _now()),
    )
    if lot_id is not None:
        q = q.where(Reservation.lot_id == lot_id)
    due = db.scalars(q.with_for_update(skip_locked=True)).all()
    for res in due:
        lot = db.scalar(select(Lot).where(Lot.id == res.lot_id, Lot.tenant_id == tenant_id).with_for_update())
        assert lot is not None
        _close(
            db,
            res,
            lot,
            status="expirada",
            to_lot=LotStatus.DISPONIVEL,
            actor=None,
            reason="reserva expirada",
        )
    db.flush()
    return len(due)


def create(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    lot_id: uuid.UUID,
    customer_id: uuid.UUID,
    broker_id: uuid.UUID | None,
    hours: int,
    actor: uuid.UUID,
    notes: str | None,
) -> Reservation:
    if (
        db.scalar(select(Customer.id).where(Customer.id == customer_id, Customer.tenant_id == tenant_id))
        is None
    ):
        raise NotFoundError("Comprador não encontrado")
    if broker_id is not None:
        broker = db.scalar(select(Broker).where(Broker.id == broker_id, Broker.tenant_id == tenant_id))
        if broker is None:
            raise NotFoundError("Corretor não encontrado")
        if not broker.is_active:
            raise ReservationError("Corretor inativo")

    lot = db.scalar(select(Lot).where(Lot.id == lot_id, Lot.tenant_id == tenant_id).with_for_update())
    if lot is None:
        raise NotFoundError("Lote não encontrado")
    expire_due(db, tenant_id, lot_id=lot.id)
    if lot.status != LotStatus.DISPONIVEL:
        raise LotUnavailableError(f"Lote não está disponível (situação atual: {lot.status})")

    res = Reservation(
        tenant_id=tenant_id,
        lot_id=lot.id,
        customer_id=customer_id,
        broker_id=broker_id,
        status=ACTIVE,
        price=lot.price,
        expires_at=_now() + timedelta(hours=hours),
        notes=notes,
        created_by=actor,
    )
    db.add(res)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise LotUnavailableError("Lote já possui reserva ativa") from None
    apply_status(
        db,
        lot,
        LotStatus.RESERVADO,
        actor_user_id=actor,
        reason=f"reserva até {res.expires_at:%d/%m %H:%M} UTC",
        extra={"reservation_id": str(res.id)},
    )
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="reservation.create",
        entity="reservation",
        entity_id=res.id,
        data={"lot_id": str(lot.id), "price": str(res.price), "hours": hours},
    )
    return res


def _lock_active(db: Session, tenant_id: uuid.UUID, res_id: uuid.UUID) -> tuple[Reservation, Lot]:
    res = db.scalar(select(Reservation).where(Reservation.id == res_id, Reservation.tenant_id == tenant_id))
    if res is None:
        raise NotFoundError("Reserva não encontrada")
    # Ordem de travas: sempre lote → reserva (mesma ordem de create), evitando deadlock.
    lot = db.scalar(select(Lot).where(Lot.id == res.lot_id, Lot.tenant_id == tenant_id).with_for_update())
    db.refresh(res, with_for_update=True)
    assert lot is not None
    if res.status == ACTIVE and res.expires_at <= _now():
        expire_due(db, tenant_id, lot_id=lot.id)
    if res.status != ACTIVE:
        raise ReservationError(f"Reserva não está ativa (situação: {res.status})")
    return res, lot


def cancel(
    db: Session, *, tenant_id: uuid.UUID, res_id: uuid.UUID, actor: uuid.UUID, reason: str
) -> Reservation:
    res, lot = _lock_active(db, tenant_id, res_id)
    _close(db, res, lot, status="cancelada", to_lot=LotStatus.DISPONIVEL, actor=actor, reason=reason)
    return res
