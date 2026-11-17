import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.auth.rbac import Role


class LoginIn(BaseModel):
    tenant_slug: str = Field(min_length=2, max_length=60)
    email: str = Field(min_length=3, max_length=254)  # login não valida formato: só compara
    password: str = Field(min_length=1, max_length=256)


class SwitchTenantIn(BaseModel):
    tenant_slug: str = Field(min_length=2, max_length=60)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class LoginOut(BaseModel):
    """Login: token de acesso OU (com verificação em duas etapas) o desafio para informar o código."""

    access_token: str | None = None
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    mfa_required: bool = False
    mfa_token: str | None = None


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=6, max_length=20)  # 6 dígitos do aplicativo ou código de recuperação


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    document: str | None = None


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    role_label: str
    permissions: list[str]
    mfa_enabled: bool = False
    mfa_recommended: bool = False  # perfis críticos (proprietário, administrador, financeiro)
    email_enabled: bool = False  # API envia e-mail (convite, "esqueci minha senha")
    tenant: TenantOut


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime
    mfa_enabled: bool = False
    invite_sent: bool = False  # convite/aviso enviado por e-mail nesta criação


class MemberCreateIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    role: Role
    # Usuário novo: sem senha inicial, recebe CONVITE por e-mail para criar a própria senha (exige e-mail
    # configurado na API); com senha inicial, ela vale como antes.
    initial_password: str | None = Field(default=None, min_length=12, max_length=256)
    reason: str | None = Field(default=None, max_length=500)


class MemberUpdateIn(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    reason: str = Field(min_length=3, max_length=500)


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    entity: str
    entity_id: str | None
    reason: str | None
    data: dict[str, Any]
    created_at: datetime
