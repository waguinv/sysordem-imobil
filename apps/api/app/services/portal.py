"""Portal do comprador (Fase 5b): convites, ativação e login.

- O acesso nasce de um CONVITE gerado pela equipe (sem envio de e-mail ainda — decisão 10: o link é
  mostrado uma única vez para quem gerou). Só o hash (sha256) do token é guardado; uso único; expira.
- Gerar novo convite invalida os anteriores e serve também para o comprador trocar a senha esquecida.
- O token de sessão do portal tem `aud` próprio e o id do comprador (`cid`): não abre rotas da equipe.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_portal_token, hash_password, verify_password
from app.models import Customer, PortalAccount, PortalToken, Tenant
from app.services import audit, login_throttle

MIN_PASSWORD = 8


class PortalError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Invite:
    account: PortalAccount
    token: str  # em claro: devolvido UMA vez
    expires_at: datetime


def invite(
    db: Session, *, tenant_id: uuid.UUID, customer: Customer, email: str | None, actor: uuid.UUID
) -> Invite:
    address = (email or customer.email or "").strip().lower()
    if "@" not in address:
        raise PortalError("Informe o e-mail do comprador para o acesso ao portal")
    acc = db.scalar(
        select(PortalAccount)
        .where(PortalAccount.tenant_id == tenant_id, PortalAccount.customer_id == customer.id)
        .with_for_update()
    )
    other = db.scalar(
        select(PortalAccount.id).where(PortalAccount.tenant_id == tenant_id, PortalAccount.email == address)
    )
    if other is not None and (acc is None or other != acc.id):
        raise PortalError("Este e-mail já é usado por outro comprador no portal")
    if acc is None:
        acc = PortalAccount(tenant_id=tenant_id, customer_id=customer.id, email=address, is_active=True)
        db.add(acc)
    acc.email = address
    acc.is_active = True
    acc.invited_at = _now()
    acc.invited_by = actor
    db.flush()
    # convites anteriores deixam de valer
    db.execute(
        update(PortalToken)
        .where(PortalToken.account_id == acc.id, PortalToken.used_at.is_(None))
        .values(used_at=_now())
        .execution_options(synchronize_session=False)
    )
    token = secrets.token_urlsafe(32)
    expires = _now() + timedelta(days=get_settings().portal_invite_days)
    db.add(
        PortalToken(
            tenant_id=tenant_id,
            account_id=acc.id,
            purpose="convite",
            token_hash=_hash(token),
            expires_at=expires,
            created_by=actor,
        )
    )
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="portal.invite",
        entity="customer",
        entity_id=customer.id,
        data={"expires_at": expires.isoformat()},  # sem token nem e-mail (minimização)
    )
    return Invite(acc, token, expires)


def active_tenant(db: Session, slug: str) -> Tenant | None:
    return db.scalar(select(Tenant).where(Tenant.slug == slug.lower(), Tenant.is_active.is_(True)))


def activate(db: Session, *, tenant: Tenant, token: str, password: str) -> str:
    """Troca o convite por uma senha e devolve o token de sessão do portal."""
    if len(password) < MIN_PASSWORD:
        raise PortalError(f"A senha precisa ter pelo menos {MIN_PASSWORD} caracteres")
    row = db.scalar(
        select(PortalToken)
        .where(PortalToken.token_hash == _hash(token), PortalToken.tenant_id == tenant.id)
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= _now():
        raise PortalError("Convite inválido ou expirado. Peça um novo à empresa.")
    acc = db.scalar(select(PortalAccount).where(PortalAccount.id == row.account_id).with_for_update())
    if acc is None or not acc.is_active:
        raise PortalError("Convite inválido ou expirado. Peça um novo à empresa.")
    row.used_at = _now()
    acc.password_hash = hash_password(password)
    acc.activated_at = acc.activated_at or _now()
    acc.last_login_at = _now()
    db.flush()
    audit.record(
        db,
        tenant_id=tenant.id,
        actor_user_id=None,
        action="portal.activate",
        entity="customer",
        entity_id=acc.customer_id,
    )
    return create_portal_token(account_id=acc.id, tenant_id=tenant.id, customer_id=acc.customer_id)


def login(db: Session, *, tenant_slug: str, email: str, password: str, tenant: Tenant | None) -> str | None:
    """Devolve o token de sessão ou None (credenciais inválidas). Mesmo tempo de resposta nos dois casos."""
    key_slug = f"portal:{tenant_slug}"
    if login_throttle.is_blocked(key_slug, email):
        raise PortalError("Muitas tentativas. Aguarde alguns minutos.")
    acc = None
    if tenant is not None:
        acc = db.scalar(
            select(PortalAccount).where(
                PortalAccount.tenant_id == tenant.id, PortalAccount.email == email.strip().lower()
            )
        )
    ok = verify_password(password, acc.password_hash if acc else None)
    if tenant is None or acc is None or not ok or not acc.is_active:
        login_throttle.register_failure(key_slug, email)
        return None
    login_throttle.reset(key_slug, email)
    acc.last_login_at = _now()
    db.flush()
    audit.record(
        db,
        tenant_id=tenant.id,
        actor_user_id=None,
        action="portal.login",
        entity="customer",
        entity_id=acc.customer_id,
    )
    return create_portal_token(account_id=acc.id, tenant_id=tenant.id, customer_id=acc.customer_id)


def pending_invite_expires(db: Session, account_id: uuid.UUID) -> datetime | None:
    return db.scalar(
        select(func.max(PortalToken.expires_at)).where(
            PortalToken.account_id == account_id,
            PortalToken.used_at.is_(None),
            PortalToken.expires_at > _now(),
        )
    )
