"""Parcelas e recebimentos (Fase 3a).

- O cronograma nasce na ASSINATURA do contrato, a partir da versão assinada (nº 0 = entrada, que vence
  na data da assinatura). Contratos assinados antes da Fase 3a ganham o cronograma na 1ª consulta.
- Recebimento: sempre de UMA parcela, pode ser parcial. Em atraso, multa e juros de mora (pro rata die)
  incidem só sobre a parte da parcela quitada agora; as taxas são as da versão do contrato.
- Lançamentos são imutáveis: corrige-se por ESTORNO (uma vez por recebimento), que devolve o valor à parcela.
- Correção monetária (IPCA/IGP-M/INCC) ainda NÃO é aplicada: depende da fonte dos índices (decisão pendente).

Travas: parcela (FOR UPDATE) antes de gravar recebimento/estorno — dois recebimentos simultâneos da mesma
parcela não passam do valor devido.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.models import ContractVersion, Installment, Payment, PaymentReversal, SalesContract
from app.services import audit
from app.services.finance import Settlement, build_schedule, quote, split_payment
from app.services.payables import on_payment, on_reversal


class ReceivableError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class ReceivableNotFoundError(ReceivableError):
    pass


def today() -> date:
    return business_today()


def _version(db: Session, c: SalesContract) -> ContractVersion:
    v = db.scalar(
        select(ContractVersion).where(
            ContractVersion.contract_id == c.id, ContractVersion.version == c.current_version
        )
    )
    assert v is not None
    return v


def generate_schedule(db: Session, c: SalesContract, *, actor: uuid.UUID | None) -> list[Installment]:
    """Gera o cronograma do contrato assinado. Idempotente: não duplica se já existir."""
    if db.scalar(select(exists().where(Installment.contract_id == c.id))):
        return []
    v = _version(db, c)
    signed_on = business_today()  # entrada vence no dia da assinatura (fuso da empresa)
    rows = build_schedule(
        total=v.total_price,
        down_payment=v.down_payment,
        n=v.installments_count,
        monthly_rate_pct=v.interest_monthly_pct,
        amortization=v.amortization,
        first_due=v.first_due_date,
        down_payment_due=signed_on,
    )
    items = [
        Installment(
            tenant_id=c.tenant_id,
            contract_id=c.id,
            contract_version=v.version,
            number=r.number,
            due_date=r.due_date,
            principal=r.principal,
            interest=r.interest,
            amount=r.amount,
            paid_principal=Decimal("0.00"),
            status="aberta",
        )
        for r in rows
    ]
    db.add_all(items)
    db.flush()
    audit.record(
        db,
        tenant_id=c.tenant_id,
        actor_user_id=actor,
        action="installments.generate",
        entity="contract",
        entity_id=c.id,
        data={"count": len(items), "total": str(sum((i.amount for i in items), Decimal("0")))},
    )
    return items


def ensure_schedule(db: Session, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> None:
    """Contratos assinados antes da Fase 3a: gera o cronograma sob trava do contrato."""
    c = db.scalar(
        select(SalesContract)
        .where(SalesContract.id == contract_id, SalesContract.tenant_id == tenant_id)
        .with_for_update()
    )
    if c is not None and c.status == "assinado":
        generate_schedule(db, c, actor=None)


@dataclass(frozen=True)
class Charges:
    late_fee_pct: Decimal
    late_interest_monthly_pct: Decimal


def charges_for(db: Session, inst: Installment) -> Charges:
    v = db.scalar(
        select(ContractVersion).where(
            ContractVersion.contract_id == inst.contract_id, ContractVersion.version == inst.contract_version
        )
    )
    assert v is not None
    return Charges(v.late_fee_pct, v.late_interest_monthly_pct)


def remaining(inst: Installment) -> Decimal:
    return inst.amount - inst.paid_principal


def quote_installment(db: Session, inst: Installment, on: date) -> Settlement:
    ch = charges_for(db, inst)
    return quote(
        remaining(inst),
        due_date=inst.due_date,
        on=on,
        late_fee_pct=ch.late_fee_pct,
        late_interest_monthly_pct=ch.late_interest_monthly_pct,
    )


def _lock_installment(db: Session, tenant_id: uuid.UUID, installment_id: uuid.UUID) -> Installment:
    inst = db.scalar(
        select(Installment)
        .where(Installment.id == installment_id, Installment.tenant_id == tenant_id)
        .with_for_update()
    )
    if inst is None:
        raise ReceivableNotFoundError("Parcela não encontrada")
    return inst


def register_payment(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    installment_id: uuid.UUID,
    amount: Decimal,
    received_on: date,
    method: str,
    notes: str | None,
    actor: uuid.UUID,
) -> Payment:
    inst = _lock_installment(db, tenant_id, installment_id)
    if inst.status != "aberta":
        raise ReceivableError(f"Parcela não está em aberto (situação: {inst.status})")
    if received_on > today():
        raise ReceivableError("Data do recebimento não pode estar no futuro")
    if amount <= 0:
        raise ReceivableError("Valor deve ser maior que zero")
    ch = charges_for(db, inst)
    try:
        s = split_payment(
            amount,
            remaining(inst),
            due_date=inst.due_date,
            on=received_on,
            late_fee_pct=ch.late_fee_pct,
            late_interest_monthly_pct=ch.late_interest_monthly_pct,
        )
    except ValueError as e:
        raise ReceivableError(str(e)) from None
    p = Payment(
        tenant_id=tenant_id,
        installment_id=inst.id,
        contract_id=inst.contract_id,
        received_on=received_on,
        amount=amount,
        principal=s.principal,
        late_fee=s.late_fee,
        late_interest=s.late_interest,
        days_late=s.days_late,
        method=method,
        notes=notes,
        created_by=actor,
    )
    db.add(p)
    inst.paid_principal += s.principal
    if inst.paid_principal == inst.amount:
        inst.status = "paga"
        inst.paid_at = received_on
    db.flush()
    on_payment(db, p)  # Fase 3b: comissão e repasse sobre o recebido
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="payment.create",
        entity="installment",
        entity_id=inst.id,
        data={
            "payment_id": str(p.id),
            "amount": str(amount),
            "principal": str(s.principal),
            "late_fee": str(s.late_fee),
            "late_interest": str(s.late_interest),
            "received_on": received_on.isoformat(),
        },
    )
    return p


def reverse_payment(
    db: Session, *, tenant_id: uuid.UUID, payment_id: uuid.UUID, reason: str, actor: uuid.UUID
) -> PaymentReversal:
    p = db.scalar(select(Payment).where(Payment.id == payment_id, Payment.tenant_id == tenant_id))
    if p is None:
        raise ReceivableNotFoundError("Recebimento não encontrado")
    inst = _lock_installment(db, tenant_id, p.installment_id)
    if db.scalar(select(exists().where(PaymentReversal.payment_id == p.id))):
        raise ReceivableError("Recebimento já estornado")
    r = PaymentReversal(tenant_id=tenant_id, payment_id=p.id, reason=reason, created_by=actor)
    db.add(r)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise ReceivableError("Recebimento já estornado") from None
    inst.paid_principal -= p.principal
    if inst.status == "paga":
        inst.status = "aberta"
        inst.paid_at = None
    db.flush()
    on_reversal(db, p)  # Fase 3b: estorna comissão e repasse gerados pelo recebimento
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="payment.reverse",
        entity="installment",
        entity_id=inst.id,
        reason=reason,
        data={"payment_id": str(p.id), "amount": str(p.amount), "principal": str(p.principal)},
    )
    return r
