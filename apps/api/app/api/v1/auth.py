import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.schemas import LoginIn, LoginOut, MfaLoginIn, SwitchTenantIn, TokenOut
from app.auth.deps import AuthContext, get_auth
from app.core.client_ip import client_ip
from app.core.config import get_settings
from app.core.db import DB_DEPENDS, set_tenant
from app.core.security import (
    MFA_TOKEN_MINUTES,
    create_access_token,
    create_mfa_token,
    decode_mfa_token,
    verify_password,
)
from app.models import Membership, Tenant, User
from app.services import audit, ip_throttle, login_throttle, mfa, passwords

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")
_BAD_CODE = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Código inválido")
_EXPIRED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="A verificação expirou. Entre de novo com a senha."
)


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
    token = create_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        membership_id=membership.id,
        password_version=passwords.password_version(user),
    )
    return TokenOut(access_token=token, expires_in=get_settings().access_token_minutes * 60)


def _active_tenant(db: Session, slug: str) -> Tenant | None:
    return db.scalar(select(Tenant).where(Tenant.slug == slug.lower(), Tenant.is_active.is_(True)))


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, db: Session = DB_DEPENDS, ip: str | None = Depends(client_ip)) -> LoginOut:
    if ip_throttle.blocked("login", ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas a partir deste endereço. Aguarde alguns minutos.",
        )
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
        ip_throttle.hit("login", ip)
        raise _INVALID

    login_throttle.reset(body.tenant_slug, body.email)
    if mfa.is_enabled(db, user.id):
        # senha certa; o token de acesso só sai depois do código do aplicativo (POST /auth/mfa/login)
        return LoginOut(
            expires_in=MFA_TOKEN_MINUTES * 60,
            mfa_required=True,
            mfa_token=create_mfa_token(
                user_id=user.id, tenant_id=tenant.id, password_version=passwords.password_version(user)
            ),
        )
    audit.record(
        db, tenant_id=tenant.id, actor_user_id=user.id, action="auth.login", entity="user", entity_id=user.id
    )
    return LoginOut(**result.model_dump())


def _mfa_key(user_id: uuid.UUID) -> tuple[str, str]:
    return ("mfa", str(user_id))  # limite de tentativas próprio da 2ª etapa, por usuário


@router.post("/mfa/login", response_model=TokenOut)
def mfa_login(body: MfaLoginIn, db: Session = DB_DEPENDS, ip: str | None = Depends(client_ip)) -> TokenOut:
    """2ª etapa do login: o código do aplicativo (ou de recuperação) troca o desafio pelo token."""
    if ip_throttle.blocked("login", ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas a partir deste endereço. Aguarde alguns minutos.",
        )
    try:
        claims = decode_mfa_token(body.mfa_token)
        user_id = uuid.UUID(claims["sub"])
        tenant_id = uuid.UUID(claims["tid"])
        token_pwv = int(claims.get("pwv", 0))
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise _EXPIRED from None
    if login_throttle.is_blocked(*_mfa_key(user_id)):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Muitas tentativas")
    user = db.get(User, user_id)
    tenant = db.get(Tenant, tenant_id)
    if user is None or not user.is_active or tenant is None or not tenant.is_active:
        raise _EXPIRED
    if not passwords.token_still_valid(user, token_pwv):
        raise _EXPIRED
    method = mfa.verify(db, user.id, body.code)
    if method is None:
        login_throttle.register_failure(*_mfa_key(user_id))
        ip_throttle.hit("login", ip)
        raise _BAD_CODE
    result = _issue(db, user=user, tenant=tenant)
    if result is None:  # vínculo desativado entre a senha e o código
        raise _EXPIRED
    login_throttle.reset(*_mfa_key(user_id))
    audit.record(
        db,
        tenant_id=tenant.id,
        actor_user_id=user.id,
        action="auth.login",
        entity="user",
        entity_id=user.id,
        data={"mfa": method},
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
