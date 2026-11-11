"""Fase 4b: contas bancárias, importação de extratos (OFX/CSV) e conciliação."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError

from app.auth.deps import AuthContext, require
from app.auth.rbac import Perm
from app.core.clock import business_today
from app.models import BankAccount, BankStatement, Reconciliation, StatementLine, User
from app.services import audit
from app.services import reconciliation as svc
from app.services.bank_statement import MAX_CHARS, StatementParseError

router = APIRouter(tags=["conciliação"])

LineStatus = Literal["pendente", "conciliada", "ignorada"]


# ─────────────────────────── DTOs ───────────────────────────


class AccountIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    bank_name: str | None = Field(default=None, max_length=80)
    branch: str | None = Field(default=None, max_length=20)
    account_number: str | None = Field(default=None, max_length=30)


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    is_active: bool | None = None


class AccountOut(BaseModel):
    id: uuid.UUID
    name: str
    bank_name: str | None
    branch: str | None
    account_number: str | None
    is_active: bool


class StatementIn(BaseModel):
    file_name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=MAX_CHARS)


class StatementOut(BaseModel):
    id: uuid.UUID
    file_name: str
    format: str
    period_start: date | None
    period_end: date | None
    lines_total: int
    lines_new: int
    auto_matched: int
    imported_by_name: str | None
    created_at: datetime


class ImportOut(StatementOut):
    duplicates: int


class TargetOut(BaseModel):
    kind: str  # recebimento | pagamento
    id: uuid.UUID
    amount: Decimal
    on: date
    label: str
    detail: str
    link_id: uuid.UUID
    exact: bool = False


class MatchOut(BaseModel):
    id: uuid.UUID
    kind: str
    target_id: uuid.UUID
    amount: Decimal
    auto: bool
    created_by_name: str | None
    created_at: datetime
    undone_at: datetime | None
    undo_reason: str | None


class LineOut(BaseModel):
    id: uuid.UUID
    bank_account_id: uuid.UUID
    posted_on: date
    amount: Decimal
    description: str
    status: str
    ignored_reason: str | None
    matched_count: int


class LineDetailOut(LineOut):
    account_name: str
    statement_file: str
    matches: list[MatchOut]
    targets: list[TargetOut]  # lançamentos ligados hoje (ativos)


class ReconcileIn(BaseModel):
    target_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SummaryOut(BaseModel):
    pending_count: int
    pending_credits: Decimal
    pending_debits: Decimal
    reconciled_count: int
    ignored_count: int
    unmatched_payments_count: int
    unmatched_payments_total: Decimal
    unmatched_payables_count: int
    unmatched_payables_total: Decimal
    since: date
    last_import_at: datetime | None


class AutoOut(BaseModel):
    matched: int


# ─────────────────────────── helpers ───────────────────────────


def _business(e: svc.ReconciliationError) -> HTTPException:
    return HTTPException(
        status_code=404 if isinstance(e, svc.ReconciliationNotFoundError) else 409, detail=str(e)
    )


def _account_out(a: BankAccount) -> AccountOut:
    return AccountOut(
        id=a.id,
        name=a.name,
        bank_name=a.bank_name,
        branch=a.branch,
        account_number=a.account_number,
        is_active=a.is_active,
    )


def _get_account(ctx: AuthContext, account_id: uuid.UUID) -> BankAccount:
    a = ctx.db.scalar(
        select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == ctx.tenant_id)
    )
    if a is None:
        raise HTTPException(status_code=404, detail="Conta bancária não encontrada")
    return a


def _active_count() -> Any:
    return (
        select(func.count())
        .where(Reconciliation.line_id == StatementLine.id, Reconciliation.undone_at.is_(None))
        .correlate(StatementLine)
        .scalar_subquery()
    )


def _line_out(ln: StatementLine, active: int) -> LineOut:
    return LineOut(
        id=ln.id,
        bank_account_id=ln.bank_account_id,
        posted_on=ln.posted_on,
        amount=ln.amount,
        description=ln.description,
        status=svc.line_status(ln, active),
        ignored_reason=ln.ignored_reason,
        matched_count=active,
    )


def _target_out(t: svc.Target, exact: bool = False) -> TargetOut:
    return TargetOut(
        kind=t.kind,
        id=t.id,
        amount=t.amount,
        on=t.on,
        label=t.label,
        detail=t.detail,
        link_id=t.link_id,
        exact=exact,
    )


def _line_detail(ctx: AuthContext, line_id: uuid.UUID) -> LineDetailOut:
    ctx.db.expire_all()
    row = ctx.db.execute(
        select(StatementLine, BankAccount.name, BankStatement.file_name)
        .join(BankAccount, BankAccount.id == StatementLine.bank_account_id)
        .join(BankStatement, BankStatement.id == StatementLine.statement_id)
        .where(StatementLine.id == line_id, StatementLine.tenant_id == ctx.tenant_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Lançamento do extrato não encontrado")
    ln, account_name, file_name = row
    recs = ctx.db.execute(
        select(Reconciliation, User.full_name)
        .outerjoin(User, User.id == Reconciliation.created_by)
        .where(Reconciliation.line_id == ln.id, Reconciliation.tenant_id == ctx.tenant_id)
        .order_by(Reconciliation.created_at.desc())
    ).all()
    active = [r for r, _ in recs if r.undone_at is None]
    targets: list[TargetOut] = []
    if active:
        pay_ids = [r.payment_id for r in active if r.payment_id]
        ev_ids = [r.payable_event_id for r in active if r.payable_event_id]
        # alvos já conciliados: busca direta (as funções do serviço só listam os livres)
        targets = [_target_out(t) for t in _linked_targets(ctx, pay_ids, ev_ids)]
    return LineDetailOut(
        **_line_out(ln, len(active)).model_dump(),
        account_name=account_name,
        statement_file=file_name,
        matches=[
            MatchOut(
                id=r.id,
                kind="recebimento" if r.payment_id else "pagamento",
                target_id=r.payment_id or r.payable_event_id,  # type: ignore[arg-type]
                amount=r.amount,
                auto=r.auto,
                created_by_name=author,
                created_at=r.created_at,
                undone_at=r.undone_at,
                undo_reason=r.undo_reason,
            )
            for r, author in recs
        ],
        targets=targets,
    )


def _linked_targets(ctx: AuthContext, pay_ids: list[uuid.UUID], ev_ids: list[uuid.UUID]) -> list[svc.Target]:
    from app.models import Customer, Installment, Payable, PayableEvent, Payment, SalesContract

    out: list[svc.Target] = []
    if pay_ids:
        for p, customer, number, n in ctx.db.execute(
            select(Payment, Customer.name, SalesContract.number, Installment.number)
            .join(Installment, Installment.id == Payment.installment_id)
            .join(SalesContract, SalesContract.id == Payment.contract_id)
            .join(Customer, Customer.id == SalesContract.customer_id)
            .where(Payment.id.in_(pay_ids), Payment.tenant_id == ctx.tenant_id)
        ).all():
            out.append(
                svc.Target(
                    "recebimento",
                    p.id,
                    p.amount,
                    p.received_on,
                    customer,
                    f"Contrato {number} · {'Entrada' if n == 0 else f'Parcela {n}'} · {p.method}",
                    p.installment_id,
                )
            )
    if ev_ids:
        for ev, pay in ctx.db.execute(
            select(PayableEvent, Payable)
            .join(Payable, Payable.id == PayableEvent.payable_id)
            .where(PayableEvent.id.in_(ev_ids), PayableEvent.tenant_id == ctx.tenant_id)
        ).all():
            out.append(
                svc.Target(
                    "pagamento",
                    ev.id,
                    pay.amount,
                    ev.event_date or pay.due_date,
                    pay.payee_name,
                    pay.description,
                    pay.id,
                )
            )
    return out


# ─────────────────────────── contas ───────────────────────────


@router.get("/bank-accounts", response_model=list[AccountOut])
def list_accounts(ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))) -> list[AccountOut]:
    rows = ctx.db.scalars(
        select(BankAccount)
        .where(BankAccount.tenant_id == ctx.tenant_id)
        .order_by(BankAccount.is_active.desc(), BankAccount.name)
    ).all()
    return [_account_out(a) for a in rows]


@router.post("/bank-accounts", response_model=AccountOut, status_code=201)
def create_account(body: AccountIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))) -> AccountOut:
    a = BankAccount(tenant_id=ctx.tenant_id, **body.model_dump())
    try:
        with ctx.db.begin_nested():
            ctx.db.add(a)
            ctx.db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Já existe conta com esse nome") from None
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="bank_account.create",
        entity="bank_account",
        entity_id=a.id,
        data={"name": a.name},
    )
    return _account_out(a)


@router.patch("/bank-accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: uuid.UUID, body: AccountPatch, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> AccountOut:
    a = _get_account(ctx, account_id)
    before = {"name": a.name, "is_active": a.is_active}
    if body.name is not None:
        a.name = body.name
    if body.is_active is not None:
        a.is_active = body.is_active
    try:
        with ctx.db.begin_nested():
            ctx.db.flush()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Já existe conta com esse nome") from None
    audit.record(
        ctx.db,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user.id,
        action="bank_account.update",
        entity="bank_account",
        entity_id=a.id,
        data={"before": before, "after": {"name": a.name, "is_active": a.is_active}},
    )
    return _account_out(a)


# ─────────────────────────── extratos ───────────────────────────


@router.get("/bank-accounts/{account_id}/statements", response_model=list[StatementOut])
def list_statements(
    account_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> list[StatementOut]:
    _get_account(ctx, account_id)
    rows = ctx.db.execute(
        select(BankStatement, User.full_name)
        .outerjoin(User, User.id == BankStatement.imported_by)
        .where(BankStatement.bank_account_id == account_id, BankStatement.tenant_id == ctx.tenant_id)
        .order_by(BankStatement.created_at.desc())
        .limit(50)
    ).all()
    return [_statement_out(s, author) for s, author in rows]


def _statement_out(s: BankStatement, author: str | None) -> StatementOut:
    return StatementOut(
        id=s.id,
        file_name=s.file_name,
        format=s.format,
        period_start=s.period_start,
        period_end=s.period_end,
        lines_total=s.lines_total,
        lines_new=s.lines_new,
        auto_matched=s.auto_matched,
        imported_by_name=author,
        created_at=s.created_at,
    )


@router.post("/bank-accounts/{account_id}/statements", response_model=ImportOut, status_code=201)
def import_statement(
    account_id: uuid.UUID, body: StatementIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> ImportOut:
    try:
        res = svc.import_statement(
            ctx.db,
            tenant_id=ctx.tenant_id,
            account_id=account_id,
            file_name=body.file_name,
            content=body.content,
            actor=ctx.user.id,
        )
    except StatementParseError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except svc.ReconciliationError as e:
        raise _business(e) from None
    ctx.db.refresh(res.statement)
    return ImportOut(
        **_statement_out(res.statement, ctx.user.full_name).model_dump(), duplicates=res.duplicates
    )


@router.post("/bank-accounts/{account_id}/auto-reconcile", response_model=AutoOut)
def auto_reconcile(
    account_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> AutoOut:
    try:
        n = svc.auto_reconcile(ctx.db, tenant_id=ctx.tenant_id, account_id=account_id, actor=ctx.user.id)
    except svc.ReconciliationError as e:
        raise _business(e) from None
    return AutoOut(matched=n)


@router.get("/bank-accounts/{account_id}/reconciliation-summary", response_model=SummaryOut)
def reconciliation_summary(
    account_id: uuid.UUID,
    days: int = Query(default=90, ge=1, le=730),
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
) -> SummaryOut:
    _get_account(ctx, account_id)
    since = business_today() - timedelta(days=days)
    s = svc.summary(ctx.db, tenant_id=ctx.tenant_id, account_id=account_id, since=since)
    return SummaryOut(**s.__dict__, since=since)


# ─────────────────────────── linhas ───────────────────────────


@router.get("/statement-lines", response_model=list[LineOut])
def list_lines(
    account_id: uuid.UUID,
    ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW)),
    status_: LineStatus | None = Query(default=None, alias="status"),
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=300, ge=1, le=1000),
) -> list[LineOut]:
    _get_account(ctx, account_id)
    active = _active_count()
    stmt = select(StatementLine, active).where(
        StatementLine.tenant_id == ctx.tenant_id, StatementLine.bank_account_id == account_id
    )
    has_rec = exists().where(Reconciliation.line_id == StatementLine.id, Reconciliation.undone_at.is_(None))
    if status_ == "ignorada":
        stmt = stmt.where(StatementLine.ignored_at.is_not(None))
    elif status_ == "conciliada":
        stmt = stmt.where(has_rec)
    elif status_ == "pendente":
        stmt = stmt.where(StatementLine.ignored_at.is_(None), ~has_rec)
    if date_from:
        stmt = stmt.where(StatementLine.posted_on >= date_from)
    if date_to:
        stmt = stmt.where(StatementLine.posted_on <= date_to)
    if q:
        stmt = stmt.where(func.lower(StatementLine.description).like(f"%{q.lower()}%"))
    rows = ctx.db.execute(
        stmt.order_by(StatementLine.posted_on.desc(), StatementLine.created_at).limit(limit)
    ).all()
    return [_line_out(ln, n) for ln, n in rows]


@router.get("/statement-lines/{line_id}", response_model=LineDetailOut)
def get_line(line_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))) -> LineDetailOut:
    return _line_detail(ctx, line_id)


@router.get("/statement-lines/{line_id}/candidates", response_model=list[TargetOut])
def line_candidates(
    line_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_VIEW))
) -> list[TargetOut]:
    try:
        found = svc.candidates(ctx.db, tenant_id=ctx.tenant_id, line_id=line_id)
    except svc.ReconciliationError as e:
        raise _business(e) from None
    return [_target_out(t, exact) for t, exact in found]


@router.post("/statement-lines/{line_id}/reconcile", response_model=LineDetailOut)
def reconcile_line(
    line_id: uuid.UUID, body: ReconcileIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> LineDetailOut:
    try:
        svc.reconcile(
            ctx.db, tenant_id=ctx.tenant_id, line_id=line_id, target_ids=body.target_ids, actor=ctx.user.id
        )
    except svc.ReconciliationError as e:
        raise _business(e) from None
    return _line_detail(ctx, line_id)


@router.post("/statement-lines/{line_id}/undo", response_model=LineDetailOut)
def undo_line(
    line_id: uuid.UUID, body: ReasonIn, ctx: AuthContext = Depends(require(Perm.FINANCE_REVERSE))
) -> LineDetailOut:
    try:
        svc.undo(ctx.db, tenant_id=ctx.tenant_id, line_id=line_id, reason=body.reason, actor=ctx.user.id)
    except svc.ReconciliationError as e:
        raise _business(e) from None
    return _line_detail(ctx, line_id)


@router.post("/statement-lines/{line_id}/ignore", response_model=LineDetailOut)
def ignore_line(
    line_id: uuid.UUID, body: ReasonIn, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> LineDetailOut:
    try:
        svc.ignore(ctx.db, tenant_id=ctx.tenant_id, line_id=line_id, reason=body.reason, actor=ctx.user.id)
    except svc.ReconciliationError as e:
        raise _business(e) from None
    return _line_detail(ctx, line_id)


@router.post("/statement-lines/{line_id}/unignore", response_model=LineDetailOut)
def unignore_line(
    line_id: uuid.UUID, ctx: AuthContext = Depends(require(Perm.FINANCE_MANAGE))
) -> LineDetailOut:
    try:
        svc.unignore(ctx.db, tenant_id=ctx.tenant_id, line_id=line_id, actor=ctx.user.id)
    except svc.ReconciliationError as e:
        raise _business(e) from None
    return _line_detail(ctx, line_id)
