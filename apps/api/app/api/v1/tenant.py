import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.v1.schemas import (
    AuditEventOut,
    MemberCreateIn,
    MemberOut,
    MemberUpdateIn,
    MeOut,
    TenantOut,
)
from app.auth.deps import AuthContext, get_auth, require
from app.auth.rbac import ROLE_LABEL, Perm, Role, can_assign, permissions_for
from app.core.security import hash_password
from app.models import AuditEvent, Membership, User
from app.services import audit

router = APIRouter(tags=["tenant"])


def _member_out(m: Membership) -> MemberOut:
    return MemberOut(
        id=m.id,
        user_id=m.user_id,
        email=m.user.email,
        full_name=m.user.full_name,
        role=m.role,
        is_active=m.is_active,
        created_at=m.created_at,
    )


@router.get("/me", response_model=MeOut)
def me(ctx: AuthContext = Depends(get_auth)) -> MeOut:
    return MeOut(
        user_id=ctx.user.id,
        email=ctx.user.email,
        full_name=ctx.user.full_name,
        role=ctx.role,
        role_label=ROLE_LABEL.get(Role(ctx.role), ctx.role),
        permissions=permissions_for(ctx.role),
        tenant=TenantOut.model_validate(ctx.tenant),
    )


@router.get("/tenant", response_model=TenantOut)
def current_tenant(ctx: AuthContext = Depends(require(Perm.TENANT_VIEW))) -> TenantOut:
    return TenantOut.model_validate(ctx.tenant)


@router.get("/members", response_model=list[MemberOut])
def list_members(ctx: AuthContext = Depends(require(Perm.MEMBERS_VIEW))) -> list[MemberOut]:
    # O filtro explícito por tenant é a 1ª barreira; o RLS do PostgreSQL é a 2ª.
    rows = ctx.db.scalars(
        select(Membership).where(Membership.tenant_id == ctx.tenant_id).order_by(Membership.created_at)
    ).all()
    return [_member_out(m) for m in rows]


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def create_member(
    body: MemberCreateIn, ctx: AuthContext = Depends(require(Perm.MEMBERS_MANAGE))
) -> MemberOut:
    db = ctx.db
    if not can_assign(ctx.role, body.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Perfil não pode ser atribuído")

    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if user is None:
        if not body.initial_password:
            raise HTTPException(status_code=422, detail="Senha inicial obrigatória para novo usuário")
        user = User(
            email=body.email.lower(),
            full_name=body.full_name,
            password_hash=hash_password(body.initial_password),
        )
        db.add(user)
        db.flush()

    exists = db.scalar(
        select(Membership).where(Membership.tenant_id == ctx.tenant_id, Membership.user_id == user.id)
    )
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Usuário já vinculado")

    m = Membership(tenant_id=ctx.tenant_id, user_id=user.id, role=body.role.value)
    db.add(m)
    db.flush()
    db.refresh(m)
    audit.record(
        db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="member.create",
        entity="membership",
        entity_id=m.id,
        reason=body.reason,
        data={"role": m.role},
    )
    return _member_out(m)


@router.patch("/members/{member_id}", response_model=MemberOut)
def update_member(
    member_id: uuid.UUID, body: MemberUpdateIn, ctx: AuthContext = Depends(require(Perm.MEMBERS_MANAGE))
) -> MemberOut:
    db = ctx.db
    m = db.scalar(select(Membership).where(Membership.id == member_id, Membership.tenant_id == ctx.tenant_id))
    if m is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vínculo não encontrado")
    if m.role == Role.OWNER and ctx.role != Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Só o proprietário altera proprietário"
        )
    if m.id == ctx.membership.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Não altere o próprio vínculo")

    before = {"role": m.role, "is_active": m.is_active}
    if body.role is not None:
        if not can_assign(ctx.role, body.role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Perfil não pode ser atribuído")
        m.role = body.role.value
    if body.is_active is not None:
        m.is_active = body.is_active
    db.flush()
    audit.record(
        db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="member.update",
        entity="membership",
        entity_id=m.id,
        reason=body.reason,
        data={"before": before, "after": {"role": m.role, "is_active": m.is_active}},
    )
    return _member_out(m)


@router.get("/audit-events", response_model=list[AuditEventOut])
def list_audit(
    ctx: AuthContext = Depends(require(Perm.AUDIT_VIEW)), limit: int = Query(50, ge=1, le=200)
) -> list[AuditEvent]:
    return list(
        ctx.db.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == ctx.tenant_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        ).all()
    )
