"""Verificação em duas etapas do próprio usuário (qualquer perfil).

Usa `get_auth` (não `require`): segurança da conta funciona mesmo com a assinatura suspensa.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.deps import AuthContext, get_auth
from app.services import audit, login_throttle
from app.services import mfa as svc

router = APIRouter(prefix="/auth/mfa", tags=["auth"])


class MfaStatusOut(BaseModel):
    enabled: bool
    available: bool  # o servidor tem a chave do cofre para guardar o segredo
    recovery_codes_left: int
    enabled_at: datetime | None


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class PasswordCodeIn(PasswordIn, CodeIn):
    pass


class SetupOut(BaseModel):
    secret: str  # chave para digitar no aplicativo
    otpauth_uri: str  # link que abre o aplicativo autenticador no celular


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]  # mostrados UMA vez


def _status(ctx: AuthContext) -> MfaStatusOut:
    s = svc.status(ctx.db, ctx.user.id)
    return MfaStatusOut(
        enabled=s.enabled,
        available=s.available,
        recovery_codes_left=s.recovery_codes_left,
        enabled_at=s.enabled_at,
    )


def _guarded(ctx: AuthContext) -> tuple[str, str]:
    """Senha errada/código errado nestas rotas contam no mesmo limite do login em 2 etapas."""
    key = ("mfa", str(ctx.user.id))
    if login_throttle.is_blocked(*key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Muitas tentativas")
    return key


def _fail(key: tuple[str, str], e: svc.MfaError) -> HTTPException:
    if isinstance(e, svc.MfaUnavailableError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    login_throttle.register_failure(*key)
    return HTTPException(status_code=422, detail=str(e))


def _audit(ctx: AuthContext, action: str) -> None:
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action=action,
        entity="user",
        entity_id=ctx.user.id,
    )


@router.get("", response_model=MfaStatusOut)
def get_status(ctx: AuthContext = Depends(get_auth)) -> MfaStatusOut:
    return _status(ctx)


@router.post("/setup", response_model=SetupOut)
def start_setup(body: PasswordIn, ctx: AuthContext = Depends(get_auth)) -> SetupOut:
    key = _guarded(ctx)
    try:
        st = svc.setup(ctx.db, ctx.user, password=body.password)
    except svc.MfaError as e:
        raise _fail(key, e) from None
    return SetupOut(secret=st.secret, otpauth_uri=st.otpauth_uri)


@router.post("/enable", response_model=RecoveryCodesOut)
def enable(body: CodeIn, ctx: AuthContext = Depends(get_auth)) -> RecoveryCodesOut:
    key = _guarded(ctx)
    try:
        codes = svc.enable(ctx.db, ctx.user, code=body.code)
    except svc.MfaError as e:
        raise _fail(key, e) from None
    _audit(ctx, "auth.mfa_enable")
    return RecoveryCodesOut(recovery_codes=codes)


@router.post("/disable", response_model=MfaStatusOut)
def disable(body: PasswordCodeIn, ctx: AuthContext = Depends(get_auth)) -> MfaStatusOut:
    key = _guarded(ctx)
    try:
        svc.disable(ctx.db, ctx.user, password=body.password, code=body.code)
    except svc.MfaError as e:
        raise _fail(key, e) from None
    _audit(ctx, "auth.mfa_disable")
    return _status(ctx)


@router.post("/recovery-codes", response_model=RecoveryCodesOut)
def new_recovery_codes(body: PasswordCodeIn, ctx: AuthContext = Depends(get_auth)) -> RecoveryCodesOut:
    key = _guarded(ctx)
    try:
        codes = svc.regenerate_codes(ctx.db, ctx.user, password=body.password, code=body.code)
    except svc.MfaError as e:
        raise _fail(key, e) from None
    _audit(ctx, "auth.mfa_recovery_codes")
    return RecoveryCodesOut(recovery_codes=codes)
