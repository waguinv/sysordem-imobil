from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.schemas import LoginIn, SwitchTenantIn, TokenOut
from app.auth.deps import AuthContext, get_auth
from app.core.config import get_settings
from app.core.db import get_db, set_tenant
from app.core.security import create_access_token, verify_password
from app.models import Membership, Tenant, User
from app.services import audit, login_throttle

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")


def _issue(db: Session, *, user: User, tenant: Tenant) -> TokenOut | None:
    """Emite token só se houver vínculo ativo do usuário com a empresa (consulta sob RLS do tenant)."""
    set_tenant(db, tenant.id)
    membership = db.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.user_id == user.id,
            Membership.is_active.is_(True),
        )
    )
    if membership is None:
        return None
    token = create_access_token(user_id=user.id, tenant_id=tenant.id, membership_id=membership.id)
    return TokenOut(access_token=token, expires_in=get_settings().access_token_minutes * 60)


def _active_tenant(db: Session, slug: str) -> Tenant | None:
    return db.scalar(select(Tenant).where(Tenant.slug == slug.lower(), Tenant.is_active.is_(True)))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    if login_throttle.is_blocked(body.tenant_slug, body.email):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Muitas tentativas")

    tenant = _active_tenant(db, body.tenant_slug)
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    password_ok = verify_password(body.password, user.password_hash if user else None)

    result = None
    if tenant and user and user.is_active and password_ok:
        result = _issue(db, user=user, tenant=tenant)
    if result is None or tenant is None or user is None:
        login_throttle.register_failure(body.tenant_slug, body.email)
        raise _INVALID

    login_throttle.reset(body.tenant_slug, body.email)
    audit.record(
        db, tenant_id=tenant.id, actor_user_id=user.id, action="auth.login", entity="user", entity_id=user.id
    )
    return result


@router.post("/switch-tenant", response_model=TokenOut)
def switch_tenant(body: SwitchTenantIn, ctx: AuthContext = Depends(get_auth)) -> TokenOut:
    db = ctx.db
    tenant = _active_tenant(db, body.tenant_slug)
    result = _issue(db, user=ctx.user, tenant=tenant) if tenant else None
    if result is None or tenant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem acesso a esta empresa")
    audit.record(
        db,
        tenant_id=tenant.id,
        actor_user_id=ctx.user.id,
        action="auth.switch_tenant",
        entity="user",
        entity_id=ctx.user.id,
        data={"from_tenant": str(ctx.tenant_id)},
    )
    return result
