"""Regras de status de lote.

Concorrência: a mudança de status trava a linha (`SELECT ... FOR UPDATE`) e confere a versão
esperada pelo cliente (controle otimista). Duas requisições simultâneas para o mesmo lote são
serializadas pelo banco; a segunda encontra versão/status novos e recebe conflito — um lote nunca
é vendido ou reservado duas vezes.
"""

import uuid
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Lot, LotStatusHistory
from app.services import audit


class LotStatus(StrEnum):
    DISPONIVEL = "disponivel"
    RESERVADO = "reservado"
    VENDIDO = "vendido"
    BLOQUEADO = "bloqueado"


# Transições de status. Saída de "vendido" só por distrato (Fase 3c) — nunca manual.
TRANSITIONS: dict[LotStatus, frozenset[LotStatus]] = {
    LotStatus.DISPONIVEL: frozenset({LotStatus.RESERVADO, LotStatus.VENDIDO, LotStatus.BLOQUEADO}),
    LotStatus.RESERVADO: frozenset({LotStatus.DISPONIVEL, LotStatus.VENDIDO}),
    LotStatus.BLOQUEADO: frozenset({LotStatus.DISPONIVEL}),
    LotStatus.VENDIDO: frozenset({LotStatus.DISPONIVEL}),
}

# Desde a Fase 2a, "reservado" só nasce de uma reserva (com comprador e validade) e um lote com
# reserva ativa só sai de "reservado" cancelando, expirando ou convertendo a reserva.


def manual_transitions(current: str, held: bool) -> list[str]:
    if held or current == LotStatus.VENDIDO:  # vendido só sai por distrato
        return []
    allowed = TRANSITIONS.get(LotStatus(current), frozenset()) - {LotStatus.RESERVADO}
    return sorted(s.value for s in allowed)


class LotNotFoundError(Exception):
    pass


class LotConflictError(Exception):
    """Versão desatualizada: alguém alterou o lote antes."""


class InvalidTransitionError(Exception):
    pass


def is_held(db: Session, tenant_id: uuid.UUID, lot_id: uuid.UUID) -> bool:
    """Lote preso a uma reserva ativa ou a um contrato em rascunho (não muda de status manualmente)."""
    from app.models import Reservation, SalesContract  # import local: evita ciclo de import

    reservation = db.scalar(
        select(Reservation.id).where(
            Reservation.tenant_id == tenant_id, Reservation.lot_id == lot_id, Reservation.status == "ativa"
        )
    )
    contract = db.scalar(
        select(SalesContract.id).where(
            SalesContract.tenant_id == tenant_id,
            SalesContract.lot_id == lot_id,
            SalesContract.status == "rascunho",
        )
    )
    return reservation is not None or contract is not None


def apply_status(
    db: Session,
    lot: Lot,
    to_status: LotStatus,
    *,
    actor_user_id: uuid.UUID | None,
    reason: str,
    extra: dict[str, str] | None = None,
) -> None:
    """Muda o status de um lote JÁ TRAVADO (FOR UPDATE), com histórico e auditoria."""
    if not can_transition(lot.status, to_status):
        raise InvalidTransitionError(f"{lot.status} → {to_status}")
    previous = lot.status
    lot.status = to_status.value
    lot.version += 1
    db.add(
        LotStatusHistory(
            tenant_id=lot.tenant_id,
            lot_id=lot.id,
            from_status=previous,
            to_status=to_status.value,
            actor_user_id=actor_user_id,
            reason=reason,
        )
    )
    db.flush()
    audit.record(
        db,
        tenant_id=lot.tenant_id,
        actor_user_id=actor_user_id,
        action="lot.status",
        entity="lot",
        entity_id=lot.id,
        reason=reason,
        data={"from": previous, "to": to_status.value, "version": lot.version, **(extra or {})},
    )


def can_transition(current: str, target: str) -> bool:
    try:
        return LotStatus(target) in TRANSITIONS[LotStatus(current)]
    except ValueError:
        return False


def change_status(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    lot_id: uuid.UUID,
    to_status: LotStatus,
    expected_version: int,
    actor_user_id: uuid.UUID,
    reason: str,
) -> Lot:
    lot = db.scalar(select(Lot).where(Lot.id == lot_id, Lot.tenant_id == tenant_id).with_for_update())
    if lot is None:
        raise LotNotFoundError
    if lot.version != expected_version:
        raise LotConflictError
    if to_status.value not in manual_transitions(lot.status, is_held(db, tenant_id, lot.id)):
        raise InvalidTransitionError(
            f"{lot.status} → {to_status} (reservas são feitas e encerradas pela tela de reservas)"
        )
    apply_status(db, lot, to_status, actor_user_id=actor_user_id, reason=reason)
    return lot
