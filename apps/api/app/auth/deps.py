"""Dependências de autenticação.

O tenant vem SEMPRE do token assinado e é revalidado no banco a cada requisição
(vínculo ativo, usuário ativo, empresa ativa). Cabeçalhos, query ou corpo com
`tenant_id` enviados pelo navegador são ignorados.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.rbac import Perm, can
from app.core.db import DB_DEPENDS, set_tenant
from app.core.security import decode_access_token
from app.models import Membership, Tenant, User

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Não autenticado",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class AuthContext:
    user: User
    tenant: Tenant
    membership: Membership
    db: Session

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def role(self) -> str:
        return self.membership.role


# Com a assinatura suspensa/cancelada, alterações ficam bloqueadas. A regra mora em `require()`: cada rota
# que precisa funcionar mesmo suspensa (pagar, reativar, mudar de plano) declara `allow_suspended=True`.
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_SUSPENDED = HTTPException(
    status_code=status.HTTP_402_PAYMENT_REQUIRED,
    detail="Assinatura suspensa: o sistema está só para consulta. Regularize em Configurações → Assinatura.",
)


def get_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = DB_DEPENDS,
) -> AuthContext:
    if creds is None or creds.scheme.lower() != "bearer":
        raise _UNAUTHORIZED
    try:
        claims = decode_access_token(creds.credentials)
        user_id = uuid.UUID(claims["sub"])
        tenant_id = uuid.UUID(claims["tid"])
        membership_id = uuid.UUID(claims["mid"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _UNAUTHORIZED from None

    set_tenant(db, tenant_id)
    membership = db.scalar(
        select(Membership).where(
            Membership.id == membership_id,
            Membership.user_id == user_id,
            Membership.tenant_id == tenant_id,
            Membership.is_active.is_(True),
        )
    )
    tenant = db.get(Tenant, tenant_id)
    if membership is None or tenant is None or not tenant.is_active or not membership.user.is_active:
        raise _UNAUTHORIZED
    return AuthContext(user=membership.user, tenant=tenant, membership=membership, db=db)


def require(perm: Perm, *, allow_suspended: bool = False) -> Callable[..., AuthContext]:
    """Permissão + (para alterações) assinatura em dia. Leituras sempre passam."""

    def _dep(request: Request, ctx: AuthContext = Depends(get_auth)) -> AuthContext:
        if not can(ctx.role, perm):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão")
        if not allow_suspended and request.method not in _READ_METHODS:
            from app.services.subscription import can_write  # import local: evita ciclo

            if not can_write(ctx.db, ctx.tenant_id):
                raise _SUSPENDED
        return ctx

    return _dep
