"""Engine, sessão e contexto de tenant por transação (PostgreSQL RLS).

Cada transação aberta por uma sessão executa `set_config('app.tenant_id', <id>, true)`.
O terceiro argumento `true` torna o valor LOCAL à transação: ao fazer COMMIT/ROLLBACK
o valor é descartado, então uma conexão devolvida ao pool nunca carrega o tenant
de outra requisição. Sem contexto, `current_setting('app.tenant_id', true)` é vazio
e as políticas RLS não retornam nenhuma linha (falha fechada).
"""

from collections.abc import Iterator
from functools import lru_cache
from uuid import UUID

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

TENANT_KEY = "tenant_id"


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction: object, connection: Connection) -> None:
    tenant_id = session.info.get(TENANT_KEY)
    value = str(tenant_id) if tenant_id else ""
    connection.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": value})


def set_tenant(session: Session, tenant_id: UUID | None) -> None:
    """Define o tenant da sessão e aplica imediatamente na transação corrente, se houver."""
    session.info[TENANT_KEY] = tenant_id
    if session.in_transaction():
        session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id) if tenant_id else ""},
        )


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
