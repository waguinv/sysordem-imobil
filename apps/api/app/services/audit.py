import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: str | uuid.UUID | None = None,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    """Registra um evento de auditoria na mesma transação da alteração auditada."""
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id else None,
        reason=reason,
        data=data or {},
    )
    db.add(event)
    db.flush()
    return event
