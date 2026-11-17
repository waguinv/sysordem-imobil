"""Senha: trocar (logado), "esqueci minha senha" e redefinir/aceitar convite pelo link do e-mail."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.auth import _active_tenant, _issue
from app.api.v1.schemas import TokenOut
from app.auth.deps import AuthContext, get_auth
from app.core.db import DB_DEPENDS, set_tenant
from app.models import Membership
from app.services import audit, email, login_throttle
from app.services import passwords as svc

router = APIRouter(prefix="/auth/password", tags=["auth"])

_BAD_LINK = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Link inválido ou expirado. Peça um novo."
)


class ChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class ForgotIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    tenant_slug: str | None = Field(default=None, max_length=60)


class ForgotOut(BaseModel):
    status: Literal["recebido"] = "recebido"
    email_enabled: bool  # sem e-mail configurado, a tela orienta a falar com o administrador


class TokenInfoOut(BaseModel):
    purpose: str  # redefinir | convite
    email: str
    full_name: str
    expires_at: datetime


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=1, max_length=256)


class ResetOut(BaseModel):
    purpose: str
    tenant_slug: str | None  # para levar ao login já com a empresa


def _error(e: svc.PasswordError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(e))


@router.post("/change", response_model=TokenOut)
def change_password(body: ChangeIn, ctx: AuthContext = Depends(get_auth)) -> TokenOut:
    """Troca a senha e devolve uma sessão NOVA (as outras sessões do usuário deixam de valer)."""
    key = ("senha", str(ctx.user.id))
    if login_throttle.is_blocked(*key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Muitas tentativas")
    try:
        svc.change(ctx.db, ctx.user, current=body.current_password, new=body.new_password)
    except svc.PasswordError as e:
        login_throttle.register_failure(*key)
        raise _error(e) from None
    login_throttle.reset(*key)
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="auth.password_change",
        entity="user",
        entity_id=ctx.user.id,
    )
    token = _issue(ctx.db, user=ctx.user, tenant=ctx.tenant)
    assert token is not None
    return token


@router.post("/forgot", response_model=ForgotOut, status_code=202)
def forgot(body: ForgotIn, background: BackgroundTasks, db: Session = DB_DEPENDS) -> ForgotOut:
    """Sempre a mesma resposta, exista ou não a conta (não revela e-mails cadastrados)."""
    out = ForgotOut(email_enabled=email.enabled())
    if not email.enabled() or not svc.allow_request(body.email):
        return out
    user = svc.find_user(db, body.email)
    if user is None or not user.is_active:
        return out
    tenant = _active_tenant(db, body.tenant_slug) if body.tenant_slug else None
    raw = svc.issue_token(db, user, purpose="redefinir", tenant_id=tenant.id if tenant else None)
    if tenant is not None:
        set_tenant(db, tenant.id)
        if db.scalar(
            select(Membership.id).where(Membership.tenant_id == tenant.id, Membership.user_id == user.id)
        ):
            audit.record(
                db,
                tenant_id=tenant.id,
                actor_user_id=None,
                action="auth.password_reset_request",
                entity="user",
                entity_id=user.id,
            )
    subject, text = svc.reset_email(user, raw)
    background.add_task(email.send, user.email, subject, text)
    return out


@router.get("/token", response_model=TokenInfoOut)
def token_info(token: str = Query(min_length=10, max_length=200), db: Session = DB_DEPENDS) -> TokenInfoOut:
    info = svc.inspect(db, token)
    if info is None:
        raise _BAD_LINK
    return TokenInfoOut(
        purpose=info.token.purpose,
        email=info.user.email,
        full_name=info.user.full_name,
        expires_at=info.token.expires_at,
    )


@router.post("/reset", response_model=ResetOut)
def reset(body: ResetIn, db: Session = DB_DEPENDS) -> ResetOut:
    """Define a senha pelo link (redefinição ou convite). Encerra as sessões abertas; o MFA continua."""
    try:
        info = svc.consume(db, body.token, new_password=body.new_password)
    except svc.PasswordError as e:
        if "Link" in str(e):
            raise _BAD_LINK from None
        raise _error(e) from None
    slug = None
    if info.token.tenant_id is not None:
        set_tenant(db, info.token.tenant_id)
        from app.models import Tenant

        tenant = db.get(Tenant, info.token.tenant_id)
        slug = tenant.slug if tenant else None
        audit.record(
            db,
            tenant_id=info.token.tenant_id,
            actor_user_id=info.user.id,
            action="auth.invite_accept" if info.token.purpose == "convite" else "auth.password_reset",
            entity="user",
            entity_id=info.user.id,
        )
    return ResetOut(purpose=info.token.purpose, tenant_slug=slug)
