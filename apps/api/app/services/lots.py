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


# Transições permitidas na Fase 1 (operação manual). Saída de "vendido" só por distrato (Fase 3).
TRANSITIONS: dict[LotStatus, frozenset[LotStatus]] = {
    LotStatus.DISPONIVEL: frozenset({LotStatus.RESERVADO, LotStatus.VENDIDO, LotStatus.BLOQUEADO}),
    LotStatus.RESERVADO: frozenset({LotStatus.DISPONIVEL, LotStatus.VENDIDO}),
    LotStatus.BLOQUEADO: frozenset({LotStatus.DISPONIVEL}),
    LotStatus.VENDIDO: frozenset(),
}


class LotNotFoundError(Exception):
    pass


class LotConflictError(Exception):
    """Versão desatualizada: alguém alterou o lote antes."""


class InvalidTransitionError(Exception):
    pass


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
    if not can_transition(lot.status, to_status):
        raise InvalidTransitionError(f"{lot.status} → {to_status}")

    previous = lot.status
    lot.status = to_status.value
    lot.version += 1
    db.add(
        LotStatusHistory(
            tenant_id=tenant_id,
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
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action="lot.status",
        entity="lot",
        entity_id=lot.id,
        reason=reason,
        data={"from": previous, "to": to_status.value, "version": lot.version},
    )
    return lot
