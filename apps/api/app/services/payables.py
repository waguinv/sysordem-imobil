"""Comissões, repasses ao proprietário da área e contas a pagar (Fase 3b).

Regras (hipóteses configuráveis — decisões 3 e 4):
- Comissão do corretor: % do corretor (`brokers.commission_pct`) ou o padrão da empresa. Base/momento
  configurável por empresa: "recebimento" (padrão: % de cada valor de parcela recebido, entrada inclusa)
  ou "assinatura" (% do valor total do contrato, creditado ao assinar).
- Repasse ao proprietário da área: `developments.owner_share_pct` de cada valor de parcela recebido.
- As regras são CONGELADAS no contrato na assinatura (mudanças posteriores valem só para novos contratos).
- A base é a parte da PARCELA recebida (sem multa/juros de mora, que ficam com a empresa).
- Estorno de recebimento gera crédito NEGATIVO equivalente (nunca apaga): se o crédito original já foi
  pago, o negativo abate do próximo acerto.
- Acerto: os créditos pendentes de um beneficiário viram UMA conta a pagar; cancelar a conta devolve os
  créditos para pendente.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import (
    Broker,
    Development,
    FinanceSettings,
    LandOwner,
    Lot,
    Participation,
    Payable,
    PayableEvent,
    Payment,
    SalesContract,
)
from app.services import audit
from app.services.finance import money

BASES = ("recebimento", "assinatura")


class PayableError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class PayableNotFoundError(PayableError):
    pass


@dataclass(frozen=True)
class Settings:
    commission_basis: str = "recebimento"
    default_commission_pct: Decimal = Decimal("5.00")
    is_default: bool = True


def get_settings(db: Session, tenant_id: uuid.UUID) -> Settings:
    row = db.get(FinanceSettings, tenant_id)
    if row is None:
        return Settings()
    return Settings(row.commission_basis, row.default_commission_pct, is_default=False)


def save_settings(
    db: Session, tenant_id: uuid.UUID, *, basis: str, default_pct: Decimal, actor: uuid.UUID, reason: str
) -> Settings:
    if basis not in BASES:
        raise PayableError("Base de comissão inválida")
    before = get_settings(db, tenant_id)
    row = db.get(FinanceSettings, tenant_id, with_for_update=True)
    if row is None:
        row = FinanceSettings(tenant_id=tenant_id, commission_basis=basis, default_commission_pct=default_pct)
        db.add(row)
    row.commission_basis = basis
    row.default_commission_pct = default_pct
    row.updated_at = datetime.now(UTC)
    row.updated_by = actor
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="finance_settings.update",
        entity="finance_settings",
        reason=reason,
        data={
            "before": {"basis": before.commission_basis, "pct": str(before.default_commission_pct)},
            "after": {"basis": basis, "pct": str(default_pct)},
        },
    )
    return get_settings(db, tenant_id)


# ─────────────────────────── créditos ───────────────────────────


def _credit(
    db: Session,
    c: SalesContract,
    *,
    kind: str,
    base: Decimal,
    pct: Decimal,
    payment_id: uuid.UUID | None,
) -> Participation | None:
    amount = money(base * pct / 100)
    if amount == 0:
        return None
    p = Participation(
        tenant_id=c.tenant_id,
        kind=kind,
        broker_id=c.broker_id if kind == "comissao" else None,
        land_owner_id=c.land_owner_id if kind == "proprietario" else None,
        contract_id=c.id,
        payment_id=payment_id,
        base=base,
        pct=pct,
        amount=amount,
    )
    db.add(p)
    return p


def snapshot_on_sign(db: Session, c: SalesContract, *, total: Decimal) -> None:
    """Congela as regras no contrato e, na base "assinatura", credita a comissão do valor total."""
    s = get_settings(db, c.tenant_id)
    if c.broker_id is not None:
        broker_pct = db.scalar(select(Broker.commission_pct).where(Broker.id == c.broker_id))
        c.commission_pct = s.default_commission_pct if broker_pct is None else broker_pct
        c.commission_basis = s.commission_basis
    dev = db.scalar(
        select(Development).join(Lot, Lot.development_id == Development.id).where(Lot.id == c.lot_id)
    )
    if dev is not None and dev.land_owner_id is not None and dev.owner_share_pct > 0:
        c.owner_share_pct = dev.owner_share_pct
        c.land_owner_id = dev.land_owner_id
    if c.commission_basis == "assinatura" and c.commission_pct:
        _credit(db, c, kind="comissao", base=total, pct=c.commission_pct, payment_id=None)
    db.flush()


def on_payment(db: Session, payment: Payment) -> None:
    """Credita comissão (base "recebimento") e repasse sobre a parte da parcela recebida."""
    c = db.scalar(select(SalesContract).where(SalesContract.id == payment.contract_id))
    if c is None or payment.principal == 0:
        return
    if c.commission_basis == "recebimento" and c.commission_pct and c.broker_id:
        _credit(db, c, kind="comissao", base=payment.principal, pct=c.commission_pct, payment_id=payment.id)
    if c.owner_share_pct and c.land_owner_id:
        _credit(
            db, c, kind="proprietario", base=payment.principal, pct=c.owner_share_pct, payment_id=payment.id
        )
    db.flush()


def on_reversal(db: Session, payment: Payment) -> None:
    """Estorno do recebimento: lança o negativo de cada crédito gerado por ele."""
    originals = db.scalars(
        select(Participation).where(
            Participation.payment_id == payment.id,
            Participation.reverses_id.is_(None),
            Participation.amount > 0,
        )
    ).all()
    for o in originals:
        db.add(
            Participation(
                tenant_id=o.tenant_id,
                kind=o.kind,
                broker_id=o.broker_id,
                land_owner_id=o.land_owner_id,
                contract_id=o.contract_id,
                payment_id=o.payment_id,
                reverses_id=o.id,
                base=-o.base,
                pct=o.pct,
                amount=-o.amount,
            )
        )
    db.flush()


# ─────────────────────────── acerto: créditos → conta a pagar ───────────────────────────


def _beneficiary_filter(kind: str, beneficiary_id: uuid.UUID) -> object:
    return (
        Participation.broker_id == beneficiary_id
        if kind == "comissao"
        else (Participation.land_owner_id == beneficiary_id)
    )


def settle(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    beneficiary_id: uuid.UUID,
    due_date: date,
    actor: uuid.UUID,
) -> Payable:
    """Transforma os créditos pendentes do beneficiário numa conta a pagar."""
    if kind == "comissao":
        b = db.scalar(select(Broker).where(Broker.id == beneficiary_id, Broker.tenant_id == tenant_id))
        if b is None:
            raise PayableNotFoundError("Corretor não encontrado")
        payee, label = b.name, "Comissão"
    else:
        lo = db.scalar(
            select(LandOwner).where(LandOwner.id == beneficiary_id, LandOwner.tenant_id == tenant_id)
        )
        if lo is None:
            raise PayableNotFoundError("Proprietário não encontrado")
        payee, label = lo.name, "Repasse ao proprietário"
    pending = db.scalars(
        select(Participation)
        .where(
            Participation.tenant_id == tenant_id,
            Participation.kind == kind,
            _beneficiary_filter(kind, beneficiary_id),  # type: ignore[arg-type]
            Participation.payable_id.is_(None),
        )
        .with_for_update()
    ).all()
    total = sum((p.amount for p in pending), Decimal("0"))
    if total <= 0:
        raise PayableError("Não há valor a pagar para este beneficiário")
    pay = Payable(
        tenant_id=tenant_id,
        description=f"{label} — {len(pending)} lançamento(s)",
        category=kind,
        payee_name=payee,
        broker_id=beneficiary_id if kind == "comissao" else None,
        land_owner_id=beneficiary_id if kind == "proprietario" else None,
        due_date=due_date,
        amount=total,
        status="aberta",
        created_by=actor,
    )
    db.add(pay)
    db.flush()
    for p in pending:
        p.payable_id = pay.id
    _event(db, pay, "criada", actor=actor)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="participations.settle",
        entity="payable",
        entity_id=pay.id,
        data={"kind": kind, "count": len(pending), "amount": str(total)},
    )
    return pay


# ─────────────────────────── contas a pagar ───────────────────────────


def _event(
    db: Session,
    pay: Payable,
    kind: str,
    *,
    actor: uuid.UUID,
    event_date: date | None = None,
    method: str | None = None,
    reason: str | None = None,
) -> None:
    db.add(
        PayableEvent(
            tenant_id=pay.tenant_id,
            payable_id=pay.id,
            kind=kind,
            event_date=event_date,
            method=method,
            reason=reason,
            created_by=actor,
        )
    )


def create_payable(db: Session, *, tenant_id: uuid.UUID, actor: uuid.UUID, **fields: object) -> Payable:
    pay = Payable(tenant_id=tenant_id, status="aberta", created_by=actor, **fields)
    db.add(pay)
    db.flush()
    _event(db, pay, "criada", actor=actor)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="payable.create",
        entity="payable",
        entity_id=pay.id,
        data={"category": pay.category, "amount": str(pay.amount), "due_date": pay.due_date.isoformat()},
    )
    return pay


def _lock(db: Session, tenant_id: uuid.UUID, payable_id: uuid.UUID) -> Payable:
    pay = db.scalar(
        select(Payable).where(Payable.id == payable_id, Payable.tenant_id == tenant_id).with_for_update()
    )
    if pay is None:
        raise PayableNotFoundError("Conta a pagar não encontrada")
    return pay


def pay_payable(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    payable_id: uuid.UUID,
    paid_on: date,
    method: str,
    today: date,
    actor: uuid.UUID,
    reason: str | None,
) -> Payable:
    pay = _lock(db, tenant_id, payable_id)
    if pay.status != "aberta":
        raise PayableError(f"Conta não está em aberto (situação: {pay.status})")
    if paid_on > today:
        raise PayableError("Data do pagamento não pode estar no futuro")
    pay.status, pay.paid_on, pay.method = "paga", paid_on, method
    _event(db, pay, "pagamento", actor=actor, event_date=paid_on, method=method, reason=reason)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="payable.pay",
        entity="payable",
        entity_id=pay.id,
        data={"amount": str(pay.amount), "paid_on": paid_on.isoformat(), "method": method},
    )
    return pay


def reverse_payable(
    db: Session, *, tenant_id: uuid.UUID, payable_id: uuid.UUID, actor: uuid.UUID, reason: str
) -> Payable:
    pay = _lock(db, tenant_id, payable_id)
    if pay.status != "paga":
        raise PayableError("Só uma conta paga pode ter o pagamento estornado")
    pay.status, pay.paid_on, pay.method = "aberta", None, None
    _event(db, pay, "estorno", actor=actor, reason=reason)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="payable.reverse",
        entity="payable",
        entity_id=pay.id,
        reason=reason,
        data={"amount": str(pay.amount)},
    )
    return pay


def cancel_payable(
    db: Session, *, tenant_id: uuid.UUID, payable_id: uuid.UUID, actor: uuid.UUID, reason: str
) -> Payable:
    pay = _lock(db, tenant_id, payable_id)
    if pay.status != "aberta":
        raise PayableError("Só conta em aberto pode ser cancelada (estorne o pagamento antes)")
    pay.status = "cancelada"
    released = db.execute(
        update(Participation)
        .where(Participation.payable_id == pay.id, Participation.tenant_id == tenant_id)
        .values(payable_id=None)
        .execution_options(synchronize_session=False)
    ).rowcount  # type: ignore[attr-defined]
    _event(db, pay, "cancelamento", actor=actor, reason=reason)
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="payable.cancel",
        entity="payable",
        entity_id=pay.id,
        reason=reason,
        data={"released_participations": int(released or 0)},
    )
    return pay


def pending_total(db: Session, tenant_id: uuid.UUID) -> Decimal:
    return db.scalar(
        select(func.coalesce(func.sum(Participation.amount), 0)).where(
            Participation.tenant_id == tenant_id, Participation.payable_id.is_(None)
        )
    ) or Decimal("0")
