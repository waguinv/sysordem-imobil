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


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    role_label: str
    permissions: list[str]
    tenant: TenantOut


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime


class MemberCreateIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    role: Role
    # Senha inicial só para usuário novo; fluxo de convite por e-mail fica para fase futura.
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
