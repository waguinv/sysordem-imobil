"""Renegociação e distrato (Fase 3c). Regras são hipóteses a validar com jurídico/contábil (decisão 5).

Renegociação
- Troca TODO o saldo em aberto do contrato (parcelas "aberta", inclusive parciais) por um novo cronograma.
- Opcional: incorporar multa e juros de mora devidos hoje; desconto em R$.
- Novas condições respeitam a política de contratos da empresa (parcelas máx., juros máx., Price/SAC).
- Parcelas antigas ficam "renegociada" (com o que já foi pago); as novas continuam a numeração.

Distrato (contrato assinado)
- Retenção: % do valor total do contrato (padrão 10% — referência da Lei 13.786/2018 para loteamentos),
  limitada ao que foi pago; o resto do pago (parcelas, sem multa/mora, líquido de estornos) é devolvido.
- A devolução vira conta a pagar ao comprador (categoria "devolucao"), com vencimento informado.
- Parcelas em aberto → "cancelada"; contrato → "distratado"; lote → "disponivel" (pode ser vendido de novo).
- Comissões e repasses já lançados não são revertidos automaticamente (decisão pendente).

Travas: lote → contrato → parcelas (mesma ordem do resto do sistema).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.models import (
    ContractVersion,
    Customer,
    Installment,
    Lot,
    Payment,
    PaymentReversal,
    Renegotiation,
    Rescission,
    SalesContract,
)
from app.services import audit
from app.services.finance import build_schedule, money
from app.services.lots import LotStatus, apply_status
from app.services.payables import create_payable
from app.services.policy import get_policy
from app.services.receivables import quote_installment, remaining


class ChangeError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class ChangeNotFoundError(ChangeError):
    pass


DEFAULT_RETENTION_PCT = Decimal("10.00")


def _lock_signed(db: Session, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> tuple[SalesContract, Lot]:
    c = db.scalar(
        select(SalesContract).where(SalesContract.id == contract_id, SalesContract.tenant_id == tenant_id)
    )
    if c is None:
        raise ChangeNotFoundError("Contrato não encontrado")
    lot = db.scalar(select(Lot).where(Lot.id == c.lot_id, Lot.tenant_id == tenant_id).with_for_update())
    assert lot is not None
    db.refresh(c, with_for_update=True)
    if c.status != "assinado":
        raise ChangeError(f"Só contrato assinado (situação atual: {c.status})")
    return c, lot


def _open_installments(db: Session, contract_id: uuid.UUID, *, lock: bool) -> list[Installment]:
    stmt = (
        select(Installment)
        .where(Installment.contract_id == contract_id, Installment.status == "aberta")
        .order_by(Installment.number)
    )
    if lock:
        stmt = stmt.with_for_update()
    return list(db.scalars(stmt).all())


# ─────────────────────────── renegociação ───────────────────────────


@dataclass(frozen=True)
class OpenBalance:
    count: int
    balance: Decimal
    late_charges: Decimal
    overdue_count: int


def open_balance(db: Session, contract_id: uuid.UUID) -> OpenBalance:
    today = business_today()
    items = _open_installments(db, contract_id, lock=False)
    charges = Decimal("0.00")
    overdue = 0
    for i in items:
        q = quote_installment(db, i, today)
        charges += q.late_fee + q.late_interest
        overdue += 1 if q.days_late > 0 else 0
    return OpenBalance(len(items), sum((remaining(i) for i in items), Decimal("0.00")), charges, overdue)


def renegotiate(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    installments_count: int,
    interest_monthly_pct: Decimal | None,
    amortization: str | None,
    first_due_date: date,
    include_late_charges: bool,
    discount: Decimal,
    actor: uuid.UUID,
    reason: str,
) -> Renegotiation:
    c, _lot = _lock_signed(db, tenant_id, contract_id)
    items = _open_installments(db, c.id, lock=True)
    if not items:
        raise ChangeError("Não há parcelas em aberto para renegociar")
    today = business_today()
    old = sum((remaining(i) for i in items), Decimal("0.00"))
    charges = Decimal("0.00")
    if include_late_charges:
        for i in items:
            q = quote_installment(db, i, today)
            charges += q.late_fee + q.late_interest
    new_balance = old + charges - discount
    if new_balance <= 0:
        raise ChangeError("Desconto não pode zerar o saldo (para quitar, registre o recebimento)")

    p = get_policy(db, tenant_id)
    rate = p.default_interest_monthly_pct if interest_monthly_pct is None else interest_monthly_pct
    amort = amortization or p.default_amortization
    if installments_count < 1 or installments_count > p.max_installments:
        raise ChangeError(f"Número de parcelas entre 1 e {p.max_installments}")
    if rate > p.max_interest_monthly_pct:
        raise ChangeError(f"Juros acima do máximo da empresa ({p.max_interest_monthly_pct}% a.m.)")
    if amort not in p.allowed_amortizations:
        raise ChangeError("Sistema de amortização não permitido pela política da empresa")
    if first_due_date < today:
        raise ChangeError("1º vencimento não pode estar no passado")

    r = Renegotiation(
        tenant_id=tenant_id,
        contract_id=c.id,
        old_balance=old,
        late_charges=charges,
        discount=discount,
        new_balance=new_balance,
        replaced_count=len(items),
        installments_count=installments_count,
        interest_monthly_pct=rate,
        amortization=amort,
        first_due_date=first_due_date,
        reason=reason,
        created_by=actor,
    )
    db.add(r)
    db.flush()
    for i in items:
        i.status = "renegociada"
    start = db.scalar(select(func.max(Installment.number)).where(Installment.contract_id == c.id)) or 0
    rows = build_schedule(
        total=new_balance,
        down_payment=Decimal("0"),
        n=installments_count,
        monthly_rate_pct=rate,
        amortization=amort,
        first_due=first_due_date,
        down_payment_due=today,
    )
    for row in rows:
        db.add(
            Installment(
                tenant_id=tenant_id,
                contract_id=c.id,
                contract_version=c.current_version,
                number=start + row.number,
                due_date=row.due_date,
                principal=row.principal,
                interest=row.interest,
                amount=row.amount,
                paid_principal=Decimal("0.00"),
                status="aberta",
                renegotiation_id=r.id,
            )
        )
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="contract.renegotiate",
        entity="contract",
        entity_id=c.id,
        reason=reason,
        data={
            "renegotiation_id": str(r.id),
            "old_balance": str(old),
            "late_charges": str(charges),
            "discount": str(discount),
            "new_balance": str(new_balance),
            "replaced": len(items),
            "installments": installments_count,
        },
    )
    return r


# ─────────────────────────── distrato ───────────────────────────


@dataclass(frozen=True)
class RescissionQuote:
    contract_total: Decimal
    paid_total: Decimal
    retention_pct: Decimal
    retention_amount: Decimal
    refund_amount: Decimal
    open_count: int


def paid_total(db: Session, contract_id: uuid.UUID) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(Payment.principal), 0))
        .outerjoin(PaymentReversal, PaymentReversal.payment_id == Payment.id)
        .where(Payment.contract_id == contract_id, PaymentReversal.id.is_(None))
    )
    return money(Decimal(total or 0))


def rescission_quote(db: Session, c: SalesContract, retention_pct: Decimal) -> RescissionQuote:
    v = db.scalar(
        select(ContractVersion).where(
            ContractVersion.contract_id == c.id, ContractVersion.version == c.current_version
        )
    )
    assert v is not None
    paid = paid_total(db, c.id)
    retention = min(paid, money(v.total_price * retention_pct / 100))
    open_count = len(_open_installments(db, c.id, lock=False))
    return RescissionQuote(v.total_price, paid, retention_pct, retention, paid - retention, open_count)


def rescind(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    retention_pct: Decimal,
    refund_due_date: date,
    actor: uuid.UUID,
    reason: str,
) -> Rescission:
    c, lot = _lock_signed(db, tenant_id, contract_id)
    items = _open_installments(db, c.id, lock=True)
    q = rescission_quote(db, c, retention_pct)
    for i in items:
        i.status = "cancelada"
    c.status = "distratado"
    apply_status(
        db,
        lot,
        LotStatus.DISPONIVEL,
        actor_user_id=actor,
        reason=f"distrato do contrato {c.number}",
        extra={"contract_id": str(c.id)},
    )
    payable_id = None
    if q.refund_amount > 0:
        customer = db.scalar(select(Customer).where(Customer.id == c.customer_id))
        assert customer is not None
        pay = create_payable(
            db,
            tenant_id=tenant_id,
            actor=actor,
            description=f"Devolução por distrato — contrato {c.number}",
            category="devolucao",
            payee_name=customer.name,
            customer_id=customer.id,
            development_id=lot.development_id,
            due_date=refund_due_date,
            amount=q.refund_amount,
            notes=f"Pago {q.paid_total}; retenção {q.retention_pct}% = {q.retention_amount}",
        )
        payable_id = pay.id
    r = Rescission(
        tenant_id=tenant_id,
        contract_id=c.id,
        paid_total=q.paid_total,
        retention_pct=retention_pct,
        retention_amount=q.retention_amount,
        refund_amount=q.refund_amount,
        payable_id=payable_id,
        reason=reason,
        created_by=actor,
    )
    db.add(r)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="contract.rescind",
        entity="contract",
        entity_id=c.id,
        reason=reason,
        data={
            "paid_total": str(q.paid_total),
            "retention": str(q.retention_amount),
            "refund": str(q.refund_amount),
            "cancelled_installments": len(items),
        },
    )
    return r
