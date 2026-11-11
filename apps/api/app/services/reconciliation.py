"""Conciliação bancária (Fase 4b).

- Linha de CRÉDITO do extrato concilia com recebimentos (`payments` não estornados); linha de DÉBITO,
  com pagamentos de contas a pagar (evento "pagamento" ainda vigente — sem estorno depois dele).
  Recebimento/pagamento em dinheiro não passa pelo banco e fica de fora.
- Uma linha pode juntar vários lançamentos (ex.: o banco credita vários boletos de uma vez), desde que
  a soma bata exatamente. Um lançamento só fica conciliado com uma linha ativa (índice único parcial).
- Automática (na importação ou sob demanda): só quando a linha tem UM candidato de mesmo valor a até
  3 dias e esse candidato não é disputado por outra linha. O resto fica para o financeiro, com sugestões.
- Nada é apagado: desfazer marca a conciliação como desfeita (com motivo); ignorar uma linha (tarifa,
  transferência entre contas…) exige motivo. Estorno de recebimento/pagamento desfaz a conciliação dele.
"""

import hashlib
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.models import (
    BankAccount,
    BankStatement,
    Customer,
    Installment,
    Payable,
    PayableEvent,
    Payment,
    PaymentReversal,
    Reconciliation,
    SalesContract,
    StatementLine,
)
from app.services import audit
from app.services.bank_statement import parse

AUTO_WINDOW_DAYS = 3
CANDIDATE_WINDOW_DAYS = 15
BANKLESS_METHODS = ("dinheiro",)


class ReconciliationError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class ReconciliationNotFoundError(ReconciliationError):
    pass


@dataclass(frozen=True)
class Target:
    kind: str  # recebimento | pagamento
    id: uuid.UUID  # payment_id ou payable_event_id
    amount: Decimal  # positivo
    on: date
    label: str
    detail: str
    link_id: uuid.UUID  # parcela (recebimento) ou conta a pagar (pagamento)


def _active_rec(**col: Any) -> Any:
    ((name, value),) = col.items()
    return exists().where(getattr(Reconciliation, name) == value, Reconciliation.undone_at.is_(None))


# ─────────────────────────── lançamentos do sistema ───────────────────────────


def payment_targets(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    ids: list[uuid.UUID] | None = None,
    max_amount: Decimal | None = None,
) -> list[Target]:
    """Recebimentos que podem ser conciliados: não estornados, não em dinheiro e ainda livres."""
    stmt = (
        select(Payment, Customer.name, SalesContract.number, Installment.number)
        .join(Installment, Installment.id == Payment.installment_id)
        .join(SalesContract, SalesContract.id == Payment.contract_id)
        .join(Customer, Customer.id == SalesContract.customer_id)
        .where(
            Payment.tenant_id == tenant_id,
            Payment.method.not_in(BANKLESS_METHODS),
            ~exists().where(PaymentReversal.payment_id == Payment.id),
            ~_active_rec(payment_id=Payment.id),
        )
    )
    if date_from:
        stmt = stmt.where(Payment.received_on >= date_from)
    if date_to:
        stmt = stmt.where(Payment.received_on <= date_to)
    if ids is not None:
        stmt = stmt.where(Payment.id.in_(ids))
    if max_amount is not None:
        stmt = stmt.where(Payment.amount <= max_amount)
    return [
        Target(
            kind="recebimento",
            id=p.id,
            amount=p.amount,
            on=p.received_on,
            label=customer,
            detail=f"Contrato {number} · {'Entrada' if n == 0 else f'Parcela {n}'} · {p.method}",
            link_id=p.installment_id,
        )
        for p, customer, number, n in db.execute(stmt.order_by(Payment.received_on)).all()
    ]


def payable_targets(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    ids: list[uuid.UUID] | None = None,
    max_amount: Decimal | None = None,
) -> list[Target]:
    """Pagamentos de contas a pagar ainda vigentes (sem estorno depois), não em dinheiro, livres."""
    later = aliased(PayableEvent)
    stmt = (
        select(PayableEvent, Payable)
        .join(Payable, Payable.id == PayableEvent.payable_id)
        .where(
            PayableEvent.tenant_id == tenant_id,
            PayableEvent.kind == "pagamento",
            Payable.status == "paga",
            (PayableEvent.method.is_(None)) | PayableEvent.method.not_in(BANKLESS_METHODS),
            ~exists().where(later.payable_id == PayableEvent.payable_id, later.seq > PayableEvent.seq),
            ~_active_rec(payable_event_id=PayableEvent.id),
        )
    )
    if date_from:
        stmt = stmt.where(PayableEvent.event_date >= date_from)
    if date_to:
        stmt = stmt.where(PayableEvent.event_date <= date_to)
    if ids is not None:
        stmt = stmt.where(PayableEvent.id.in_(ids))
    if max_amount is not None:
        stmt = stmt.where(Payable.amount <= max_amount)
    out = []
    for ev, pay in db.execute(stmt.order_by(PayableEvent.event_date)).all():
        assert ev.event_date is not None
        out.append(
            Target(
                kind="pagamento",
                id=ev.id,
                amount=pay.amount,
                on=ev.event_date,
                label=pay.payee_name,
                detail=pay.description,
                link_id=pay.id,
            )
        )
    return out


def _targets_for(db: Session, line: StatementLine, **kw: Any) -> list[Target]:
    fn = payment_targets if line.amount > 0 else payable_targets
    return fn(db, line.tenant_id, **kw)


# ─────────────────────────── contas e importação ───────────────────────────


def _account(db: Session, tenant_id: uuid.UUID, account_id: uuid.UUID) -> BankAccount:
    a = db.scalar(select(BankAccount).where(BankAccount.id == account_id, BankAccount.tenant_id == tenant_id))
    if a is None:
        raise ReconciliationNotFoundError("Conta bancária não encontrada")
    return a


@dataclass(frozen=True)
class ImportResult:
    statement: BankStatement
    duplicates: int


def import_statement(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    file_name: str,
    content: str,
    actor: uuid.UUID,
) -> ImportResult:
    acc = _account(db, tenant_id, account_id)
    if not acc.is_active:
        raise ReconciliationError("Conta bancária inativa")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    prev = db.scalar(
        select(BankStatement).where(
            BankStatement.tenant_id == tenant_id,
            BankStatement.bank_account_id == acc.id,
            BankStatement.file_sha256 == digest,
        )
    )
    if prev is not None:
        raise ReconciliationError(
            f"Este arquivo já foi importado nesta conta em {prev.created_at.date().strftime('%d/%m/%Y')}"
        )
    parsed = parse(content)
    st = BankStatement(
        tenant_id=tenant_id,
        bank_account_id=acc.id,
        file_name=file_name[:200] or "extrato",
        file_sha256=digest,
        format=parsed.format,
        period_start=min(ln.posted_on for ln in parsed.lines),
        period_end=max(ln.posted_on for ln in parsed.lines),
        lines_total=len(parsed.lines),
        lines_new=0,
        imported_by=actor,
    )
    try:
        with db.begin_nested():
            db.add(st)
            db.flush()
    except IntegrityError:
        raise ReconciliationError("Este arquivo já foi importado nesta conta") from None
    new = 0
    for ln in parsed.lines:
        res = db.execute(
            insert(StatementLine)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                bank_account_id=acc.id,
                statement_id=st.id,
                posted_on=ln.posted_on,
                amount=ln.amount,
                description=ln.description,
                fit_id=ln.fit_id,
            )
            .on_conflict_do_nothing(constraint="uq_statement_lines_fit")
            .returning(StatementLine.id)
        )
        new += res.first() is not None
    st.lines_new = new
    db.flush()
    st.auto_matched = auto_reconcile(db, tenant_id=tenant_id, account_id=acc.id, actor=actor, audit_it=False)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="statement.import",
        entity="bank_account",
        entity_id=acc.id,
        data={
            "statement_id": str(st.id),
            "file_name": st.file_name,
            "format": st.format,
            "lines_total": st.lines_total,
            "lines_new": new,
            "auto_matched": st.auto_matched,
        },
    )
    return ImportResult(st, st.lines_total - new)


# ─────────────────────────── conciliação ───────────────────────────


def _pending_filter() -> Any:
    return and_(StatementLine.ignored_at.is_(None), ~_active_rec(line_id=StatementLine.id))


def auto_reconcile(
    db: Session, *, tenant_id: uuid.UUID, account_id: uuid.UUID, actor: uuid.UUID, audit_it: bool = True
) -> int:
    _account(db, tenant_id, account_id)
    lines = db.scalars(
        select(StatementLine)
        .where(
            StatementLine.tenant_id == tenant_id,
            StatementLine.bank_account_id == account_id,
            _pending_filter(),
        )
        .order_by(StatementLine.posted_on)
        .with_for_update()
    ).all()
    if not lines:
        return 0
    window = timedelta(days=AUTO_WINDOW_DAYS)
    lo = min(ln.posted_on for ln in lines) - window
    hi = max(ln.posted_on for ln in lines) + window
    pools = {
        True: payment_targets(db, tenant_id, date_from=lo, date_to=hi),
        False: payable_targets(db, tenant_id, date_from=lo, date_to=hi),
    }
    cands: dict[uuid.UUID, list[Target]] = {}
    usage: Counter[uuid.UUID] = Counter()
    for ln in lines:
        found = [
            t
            for t in pools[ln.amount > 0]
            if t.amount == abs(ln.amount) and abs((t.on - ln.posted_on).days) <= AUTO_WINDOW_DAYS
        ]
        cands[ln.id] = found
        usage.update(t.id for t in found)
    matched = 0
    for ln in lines:
        found = cands[ln.id]
        if len(found) != 1 or usage[found[0].id] != 1:
            continue
        t = found[0]
        try:
            with db.begin_nested():
                db.add(_rec(ln, t, actor=actor, auto=True))
                db.flush()
            matched += 1
        except IntegrityError:
            continue  # conciliado em paralelo por outra requisição
    if matched and audit_it:
        audit.record(
            db,
            tenant_id=tenant_id,
            actor_user_id=actor,
            action="reconciliation.auto",
            entity="bank_account",
            entity_id=account_id,
            data={"matched": matched},
        )
    return matched


def _rec(line: StatementLine, t: Target, *, actor: uuid.UUID, auto: bool) -> Reconciliation:
    return Reconciliation(
        tenant_id=line.tenant_id,
        line_id=line.id,
        payment_id=t.id if t.kind == "recebimento" else None,
        payable_event_id=t.id if t.kind == "pagamento" else None,
        amount=t.amount,
        auto=auto,
        created_by=actor,
    )


def _lock_line(db: Session, tenant_id: uuid.UUID, line_id: uuid.UUID) -> StatementLine:
    ln = db.scalar(
        select(StatementLine)
        .where(StatementLine.id == line_id, StatementLine.tenant_id == tenant_id)
        .with_for_update()
    )
    if ln is None:
        raise ReconciliationNotFoundError("Lançamento do extrato não encontrado")
    return ln


def active_recs(db: Session, line_id: uuid.UUID) -> list[Reconciliation]:
    return list(
        db.scalars(
            select(Reconciliation).where(
                Reconciliation.line_id == line_id, Reconciliation.undone_at.is_(None)
            )
        ).all()
    )


def line_status(line: StatementLine, active: int) -> str:
    if line.ignored_at is not None:
        return "ignorada"
    return "conciliada" if active else "pendente"


def candidates(db: Session, *, tenant_id: uuid.UUID, line_id: uuid.UUID) -> list[tuple[Target, bool]]:
    """Sugestões para conciliar à mão: mesmo tipo, até 15 dias, valor ≤ o da linha. Exatos primeiro."""
    ln = db.scalar(
        select(StatementLine).where(StatementLine.id == line_id, StatementLine.tenant_id == tenant_id)
    )
    if ln is None:
        raise ReconciliationNotFoundError("Lançamento do extrato não encontrado")
    window = timedelta(days=CANDIDATE_WINDOW_DAYS)
    value = abs(ln.amount)
    found = _targets_for(
        db, ln, date_from=ln.posted_on - window, date_to=ln.posted_on + window, max_amount=value
    )
    found.sort(key=lambda t: (t.amount != value, abs((t.on - ln.posted_on).days)))
    return [(t, t.amount == value) for t in found[:50]]


def reconcile(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    line_id: uuid.UUID,
    target_ids: list[uuid.UUID],
    actor: uuid.UUID,
) -> StatementLine:
    ln = _lock_line(db, tenant_id, line_id)
    if ln.ignored_at is not None:
        raise ReconciliationError("Lançamento ignorado — desfaça o ignorar antes de conciliar")
    if active_recs(db, ln.id):
        raise ReconciliationError("Lançamento já conciliado — desfaça antes de conciliar de novo")
    ids = list(dict.fromkeys(target_ids))
    if not ids:
        raise ReconciliationError("Escolha ao menos um lançamento do sistema")
    found = _targets_for(db, ln, ids=ids)
    if len(found) != len(ids):
        kind = "recebimentos" if ln.amount > 0 else "pagamentos de contas"
        raise ReconciliationError(
            f"Linha de {'crédito' if ln.amount > 0 else 'débito'} só concilia com {kind} — algum lançamento "
            "escolhido não existe, foi estornado, é em dinheiro ou já está conciliado"
        )
    total = sum((t.amount for t in found), Decimal("0"))
    if total != abs(ln.amount):
        raise ReconciliationError(
            f"A soma escolhida (R$ {total}) difere do valor da linha do extrato (R$ {abs(ln.amount)})"
        )
    try:
        with db.begin_nested():
            for t in found:
                db.add(_rec(ln, t, actor=actor, auto=False))
            db.flush()
    except IntegrityError:
        raise ReconciliationError("Algum lançamento já foi conciliado com outra linha") from None
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="reconciliation.create",
        entity="statement_line",
        entity_id=ln.id,
        data={"targets": [str(t.id) for t in found], "amount": str(ln.amount)},
    )
    return ln


def _undo(recs: list[Reconciliation], reason: str, actor: uuid.UUID | None) -> None:
    now = datetime.now(UTC)
    for r in recs:
        r.undone_at, r.undone_by, r.undo_reason = now, actor, reason


def undo(
    db: Session, *, tenant_id: uuid.UUID, line_id: uuid.UUID, reason: str, actor: uuid.UUID
) -> StatementLine:
    ln = _lock_line(db, tenant_id, line_id)
    recs = active_recs(db, ln.id)
    if not recs:
        raise ReconciliationError("Lançamento não está conciliado")
    _undo(recs, reason, actor)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="reconciliation.undo",
        entity="statement_line",
        entity_id=ln.id,
        reason=reason,
        data={"reconciliations": [str(r.id) for r in recs]},
    )
    return ln


def undo_for_payment(db: Session, payment: Payment, *, reason: str, actor: uuid.UUID | None) -> None:
    """Estorno de recebimento: a linha do extrato volta a pendente (o grupo inteiro da linha)."""
    _undo_lines_of(db, payment.tenant_id, Reconciliation.payment_id == payment.id, reason, actor)


def undo_for_payable(db: Session, payable: Payable, *, reason: str, actor: uuid.UUID | None) -> None:
    ev_ids = select(PayableEvent.id).where(PayableEvent.payable_id == payable.id)
    _undo_lines_of(db, payable.tenant_id, Reconciliation.payable_event_id.in_(ev_ids), reason, actor)


def _undo_lines_of(
    db: Session, tenant_id: uuid.UUID, cond: Any, reason: str, actor: uuid.UUID | None
) -> None:
    line_ids = db.scalars(
        select(Reconciliation.line_id).where(
            Reconciliation.tenant_id == tenant_id, Reconciliation.undone_at.is_(None), cond
        )
    ).all()
    for line_id in set(line_ids):
        recs = list(
            db.scalars(
                select(Reconciliation)
                .where(Reconciliation.line_id == line_id, Reconciliation.undone_at.is_(None))
                .with_for_update()
            ).all()
        )
        _undo(recs, reason, actor)
        audit.record(
            db,
            tenant_id=tenant_id,
            actor_user_id=actor,
            action="reconciliation.undo",
            entity="statement_line",
            entity_id=line_id,
            reason=reason,
            data={"reconciliations": [str(r.id) for r in recs], "automatic": True},
        )
    db.flush()


def ignore(
    db: Session, *, tenant_id: uuid.UUID, line_id: uuid.UUID, reason: str, actor: uuid.UUID
) -> StatementLine:
    ln = _lock_line(db, tenant_id, line_id)
    if ln.ignored_at is not None:
        raise ReconciliationError("Lançamento já está ignorado")
    if active_recs(db, ln.id):
        raise ReconciliationError("Lançamento conciliado não pode ser ignorado — desfaça antes")
    ln.ignored_at, ln.ignored_by, ln.ignored_reason = datetime.now(UTC), actor, reason
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="statement_line.ignore",
        entity="statement_line",
        entity_id=ln.id,
        reason=reason,
        data={"amount": str(ln.amount)},
    )
    return ln


def unignore(db: Session, *, tenant_id: uuid.UUID, line_id: uuid.UUID, actor: uuid.UUID) -> StatementLine:
    ln = _lock_line(db, tenant_id, line_id)
    if ln.ignored_at is None:
        raise ReconciliationError("Lançamento não está ignorado")
    previous = ln.ignored_reason
    ln.ignored_at, ln.ignored_by, ln.ignored_reason = None, None, None
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="statement_line.unignore",
        entity="statement_line",
        entity_id=ln.id,
        data={"previous_reason": previous},
    )
    return ln


# ─────────────────────────── resumo ───────────────────────────


@dataclass(frozen=True)
class Summary:
    pending_count: int
    pending_credits: Decimal
    pending_debits: Decimal
    reconciled_count: int
    ignored_count: int
    unmatched_payments_count: int
    unmatched_payments_total: Decimal
    unmatched_payables_count: int
    unmatched_payables_total: Decimal
    last_import_at: datetime | None


def summary(db: Session, *, tenant_id: uuid.UUID, account_id: uuid.UUID, since: date) -> Summary:
    zero = Decimal("0")
    base = (StatementLine.tenant_id == tenant_id, StatementLine.bank_account_id == account_id)
    pending = _pending_filter()
    row = db.execute(
        select(
            func.count().filter(pending),
            func.coalesce(func.sum(StatementLine.amount).filter(pending & (StatementLine.amount > 0)), zero),
            func.coalesce(func.sum(StatementLine.amount).filter(pending & (StatementLine.amount < 0)), zero),
            func.count().filter(_active_rec(line_id=StatementLine.id)),
            func.count().filter(StatementLine.ignored_at.is_not(None)),
        ).where(*base)
    ).one()
    pays = payment_targets(db, tenant_id, date_from=since)
    pables = payable_targets(db, tenant_id, date_from=since)
    last = db.scalar(
        select(func.max(BankStatement.created_at)).where(
            BankStatement.tenant_id == tenant_id, BankStatement.bank_account_id == account_id
        )
    )
    return Summary(
        pending_count=row[0],
        pending_credits=row[1],
        pending_debits=-row[2],
        reconciled_count=row[3],
        ignored_count=row[4],
        unmatched_payments_count=len(pays),
        unmatched_payments_total=sum((t.amount for t in pays), zero),
        unmatched_payables_count=len(pables),
        unmatched_payables_total=sum((t.amount for t in pables), zero),
        last_import_at=last,
    )
