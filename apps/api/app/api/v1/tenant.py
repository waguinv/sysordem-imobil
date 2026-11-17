import secrets
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
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
from app.services import audit, email, mfa, passwords

router = APIRouter(tags=["tenant"])


# Perfis em que a verificação em duas etapas é recomendada (acesso a dinheiro e à equipe)
MFA_RECOMMENDED = {Role.OWNER, Role.ADMIN, Role.FINANCEIRO}


def _member_out(m: Membership, mfa_on: bool = False) -> MemberOut:
    return MemberOut(
        id=m.id,
        user_id=m.user_id,
        email=m.user.email,
        full_name=m.user.full_name,
        role=m.role,
        is_active=m.is_active,
        created_at=m.created_at,
        mfa_enabled=mfa_on,
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
        mfa_enabled=mfa.is_enabled(ctx.db, ctx.user.id),
        mfa_recommended=ctx.role in MFA_RECOMMENDED,
        email_enabled=email.enabled(),
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
    on = mfa.enabled_users(ctx.db, [m.user_id for m in rows])
    return [_member_out(m, m.user_id in on) for m in rows]


def _plan_users(ctx: AuthContext) -> None:
    """Fase 5c: limite de usuários ativos do plano."""
    from app.services.subscription import LimitError, check_limit

    try:
        check_limit(ctx.db, ctx.tenant_id, "users", 1)
    except LimitError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from None


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def create_member(
    body: MemberCreateIn,
    background: BackgroundTasks,
    ctx: AuthContext = Depends(require(Perm.MEMBERS_MANAGE)),
) -> MemberOut:
    db = ctx.db
    if not can_assign(ctx.role, body.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Perfil não pode ser atribuído")

    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    invite = False
    new_user = user is None
    if user is None:
        if not body.initial_password and not email.enabled():
            raise HTTPException(
                status_code=422,
                detail="Defina uma senha inicial: o envio de convite por e-mail não está configurado",
            )
        if body.initial_password:
            try:
                passwords.validate(body.initial_password, email=body.email)
            except passwords.PasswordError as e:
                raise HTTPException(status_code=422, detail=str(e)) from None
        invite = not body.initial_password
        user = User(
            email=body.email.lower(),
            full_name=body.full_name,
            # convite: senha aleatória que ninguém conhece até a pessoa criar a dela pelo link
            password_hash=hash_password(body.initial_password or secrets.token_urlsafe(32)),
        )
        db.add(user)
        db.flush()

    exists = db.scalar(
        select(Membership).where(Membership.tenant_id == ctx.tenant_id, Membership.user_id == user.id)
    )
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Usuário já vinculado")

    _plan_users(ctx)
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
        data={"role": m.role, "invite": invite},
    )
    sent = False
    if invite:
        raw = passwords.issue_token(db, user, purpose="convite", tenant_id=ctx.tenant_id)
        subject, text = passwords.invite_email(
            user, raw, company=ctx.tenant.name, invited_by=ctx.user.full_name
        )
        background.add_task(email.send, user.email, subject, text)
        sent = True
    elif not new_user and email.enabled():  # já tinha conta: só avisa do novo acesso
        subject, text = passwords.access_added_email(
            user, company=ctx.tenant.name, invited_by=ctx.user.full_name, slug=ctx.tenant.slug
        )
        background.add_task(email.send, user.email, subject, text)
        sent = True
    out = _member_out(m)
    out.invite_sent = sent
    return out


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
        if body.is_active and not m.is_active:
            _plan_users(ctx)
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
