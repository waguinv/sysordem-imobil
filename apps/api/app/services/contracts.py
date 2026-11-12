"""Contratos de venda.

Fluxo (Fase 2b):
  reserva ativa ──gerar contrato──▶ contrato "rascunho" (reserva vira "convertida"; lote segue "reservado")
  rascunho ──nova versão──▶ rascunho (condições versionadas, imutáveis)
  rascunho ──assinar──▶ "assinado" (lote → "vendido")
  rascunho ──cancelar──▶ "cancelado" (lote → "disponivel")
Contrato assinado só se desfaz por distrato (Fase 3).

Travas: sempre lote → reserva/contrato (mesma ordem em todo o sistema), evitando deadlock.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ContractVersion, Lot, Reservation, SalesContract
from app.services import audit
from app.services.finance import first_installment, money
from app.services.lots import LotStatus, apply_status
from app.services.policy import Policy, get_policy
from app.services.reservations import expire_due


class ContractError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class ContractNotFoundError(ContractError):
    pass


@dataclass(frozen=True)
class Terms:
    """Condições pedidas. Campos None usam o padrão da política da empresa."""

    total_price: Decimal | None
    down_payment: Decimal
    installments_count: int
    interest_monthly_pct: Decimal | None
    first_due_date: date | None
    adjustment_index: str | None
    late_fee_pct: Decimal | None
    late_interest_monthly_pct: Decimal | None
    notes: str | None
    amortization: str | None = None


@dataclass(frozen=True)
class Resolved:
    """Condições finais, já com os padrões da política aplicados e validadas."""

    interest_monthly_pct: Decimal
    amortization: str
    adjustment_index: str
    late_fee_pct: Decimal
    late_interest_monthly_pct: Decimal
    installment_amount: Decimal


def _resolve(t: Terms, total: Decimal, p: Policy) -> Resolved:
    """Aplica a política da empresa (padrões e limites) e calcula a 1ª parcela."""
    interest = p.default_interest_monthly_pct if t.interest_monthly_pct is None else t.interest_monthly_pct
    amort = t.amortization or p.default_amortization
    index = t.adjustment_index or p.default_adjustment_index
    late_fee = p.late_fee_pct if t.late_fee_pct is None else t.late_fee_pct
    late_int = (
        p.late_interest_monthly_pct if t.late_interest_monthly_pct is None else t.late_interest_monthly_pct
    )

    _check_basic(t, total)
    if interest > p.max_interest_monthly_pct:
        raise ContractError(f"Juros acima do máximo da empresa ({p.max_interest_monthly_pct}% a.m.)")
    if amort not in p.allowed_amortizations:
        raise ContractError("Sistema de amortização não permitido pela política da empresa")
    if t.installments_count > p.max_installments:
        raise ContractError(f"Número de parcelas acima do máximo da empresa ({p.max_installments})")
    if t.down_payment * 100 < total * p.min_down_payment_pct:
        raise ContractError(f"Entrada abaixo do mínimo da empresa ({p.min_down_payment_pct}% do total)")
    if not p.allow_override_late_charges and (
        late_fee != p.late_fee_pct or late_int != p.late_interest_monthly_pct
    ):
        raise ContractError(
            "Multa e juros de mora seguem a política da empresa e não podem ser alterados aqui"
        )
    amount = first_installment(total - t.down_payment, interest, t.installments_count, amort)
    return Resolved(interest, amort, index, late_fee, late_int, amount)


def evaluate_terms(db: Session, tenant_id: uuid.UUID, terms: Terms, total: Decimal) -> Resolved:
    """Valida condições contra a política da empresa (usado também por propostas, Fase 2c)."""
    return _resolve(terms, total, get_policy(db, tenant_id))


def _check_basic(t: Terms, total: Decimal) -> None:
    if total <= 0:
        raise ContractError("Valor total deve ser maior que zero")
    if t.down_payment > total:
        raise ContractError("Entrada não pode ser maior que o valor total")
    balance = total - t.down_payment
    if t.installments_count == 0 and balance != 0:
        raise ContractError("Sem parcelas, a entrada deve ser igual ao valor total (venda à vista)")
    if t.installments_count > 0 and balance == 0:
        raise ContractError("Com a entrada igual ao total, não há parcelas")
    if t.installments_count > 0 and t.first_due_date is None:
        raise ContractError("Informe o vencimento da 1ª parcela")
    if t.first_due_date is not None and t.first_due_date < datetime.now(UTC).date():
        raise ContractError("Vencimento da 1ª parcela não pode estar no passado")


def _next_number(db: Session, tenant_id: uuid.UUID) -> str:
    year = datetime.now(UTC).year
    n = db.execute(
        text(
            "INSERT INTO tenant_counters (tenant_id, key, value) VALUES (:t, :k, 1) "
            "ON CONFLICT (tenant_id, key) DO UPDATE SET value = tenant_counters.value + 1 "
            "RETURNING value"
        ),
        {"t": tenant_id, "k": f"contract:{year}"},
    ).scalar_one()
    return f"{year}-{n:04d}"


def _add_version(
    db: Session,
    c: SalesContract,
    version: int,
    t: Terms,
    total: Decimal,
    actor: uuid.UUID,
    reason: str | None,
) -> ContractVersion:
    r = _resolve(t, total, get_policy(db, c.tenant_id))
    v = ContractVersion(
        tenant_id=c.tenant_id,
        contract_id=c.id,
        version=version,
        total_price=money(total),
        down_payment=money(t.down_payment),
        installments_count=t.installments_count,
        installment_amount=r.installment_amount,
        amortization=r.amortization,
        interest_monthly_pct=r.interest_monthly_pct,
        first_due_date=t.first_due_date if t.installments_count else None,
        adjustment_index=r.adjustment_index,
        late_fee_pct=r.late_fee_pct,
        late_interest_monthly_pct=r.late_interest_monthly_pct,
        notes=t.notes,
        reason=reason,
        created_by=actor,
    )
    db.add(v)
    c.current_version = version
    db.flush()
    return v


def _lock_lot(db: Session, tenant_id: uuid.UUID, lot_id: uuid.UUID) -> Lot:
    lot = db.scalar(select(Lot).where(Lot.id == lot_id, Lot.tenant_id == tenant_id).with_for_update())
    if lot is None:
        raise ContractNotFoundError("Lote não encontrado")
    return lot


def create_from_reservation(
    db: Session, *, tenant_id: uuid.UUID, reservation_id: uuid.UUID, terms: Terms, actor: uuid.UUID
) -> SalesContract:
    res = db.scalar(
        select(Reservation).where(Reservation.id == reservation_id, Reservation.tenant_id == tenant_id)
    )
    if res is None:
        raise ContractNotFoundError("Reserva não encontrada")
    lot = _lock_lot(db, tenant_id, res.lot_id)
    expire_due(db, tenant_id, lot_id=lot.id)
    db.refresh(res, with_for_update=True)
    if res.status != "ativa":
        raise ContractError(f"Reserva não está ativa (situação: {res.status})")

    total = terms.total_price if terms.total_price is not None else res.price
    _resolve(terms, total, get_policy(db, tenant_id))  # falha antes de consumir número de contrato
    c = SalesContract(
        tenant_id=tenant_id,
        number=_next_number(db, tenant_id),
        lot_id=lot.id,
        customer_id=res.customer_id,
        broker_id=res.broker_id,
        reservation_id=res.id,
        status="rascunho",
        current_version=1,
        created_by=actor,
    )
    db.add(c)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise ContractError("Já existe contrato em aberto para este lote ou reserva") from None
    _add_version(db, c, 1, terms, total, actor, "versão inicial")

    res.status = "convertida"
    res.closed_at = datetime.now(UTC)
    res.close_reason = f"contrato {c.number}"
    db.flush()
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="contract.create",
        entity="contract",
        entity_id=c.id,
        data={"number": c.number, "reservation_id": str(res.id), "total": str(total)},
    )
    return c


def _lock_contract(db: Session, tenant_id: uuid.UUID, contract_id: uuid.UUID) -> tuple[SalesContract, Lot]:
    c = db.scalar(
        select(SalesContract).where(SalesContract.id == contract_id, SalesContract.tenant_id == tenant_id)
    )
    if c is None:
        raise ContractNotFoundError("Contrato não encontrado")
    lot = _lock_lot(db, tenant_id, c.lot_id)
    db.refresh(c, with_for_update=True)
    return c, lot


def _require_draft(c: SalesContract) -> None:
    if c.status != "rascunho":
        raise ContractError(f"Contrato não está em rascunho (situação: {c.status})")


def new_version(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    terms: Terms,
    expected_version: int,
    actor: uuid.UUID,
    reason: str,
) -> SalesContract:
    c, _lot = _lock_contract(db, tenant_id, contract_id)
    _require_draft(c)
    if c.current_version != expected_version:
        raise ContractError("O contrato foi alterado por outra pessoa. Recarregue a página.")
    current = db.scalar(
        select(ContractVersion).where(
            ContractVersion.contract_id == c.id, ContractVersion.version == c.current_version
        )
    )
    assert current is not None
    total = terms.total_price if terms.total_price is not None else current.total_price
    _add_version(db, c, c.current_version + 1, terms, total, actor, reason)
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="contract.version",
        entity="contract",
        entity_id=c.id,
        reason=reason,
        data={"version": c.current_version},
    )
    return c


def sign(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    contract_id: uuid.UUID,
    expected_version: int,
    actor: uuid.UUID,
    reason: str,
) -> SalesContract:
    """Registra a assinatura (manual nesta fase; assinatura eletrônica entra como integração futura)."""
    c, lot = _lock_contract(db, tenant_id, contract_id)
    _require_draft(c)
    if c.current_version != expected_version:
        raise ContractError("As condições mudaram desde que você abriu o contrato. Revise antes de assinar.")
    c.status = "assinado"
    c.signed_at = datetime.now(UTC)
    c.signed_by = actor
    apply_status(
        db,
        lot,
        LotStatus.VENDIDO,
        actor_user_id=actor,
        reason=f"contrato {c.number} assinado",
        extra={"contract_id": str(c.id)},
    )
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="contract.sign",
        entity="contract",
        entity_id=c.id,
        reason=reason,
        data={"version": c.current_version},
    )
    return c


def cancel(
    db: Session, *, tenant_id: uuid.UUID, contract_id: uuid.UUID, actor: uuid.UUID, reason: str
) -> SalesContract:
    c, lot = _lock_contract(db, tenant_id, contract_id)
    if c.status == "assinado":
        raise ContractError("Contrato assinado só pode ser desfeito por distrato (Fase 3)")
    _require_draft(c)
    c.status = "cancelado"
    c.cancelled_at = datetime.now(UTC)
    c.cancel_reason = reason
    if lot.status == LotStatus.RESERVADO:
        apply_status(
            db,
            lot,
            LotStatus.DISPONIVEL,
            actor_user_id=actor,
            reason=f"contrato {c.number} cancelado",
            extra={"contract_id": str(c.id)},
        )
    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="contract.cancel",
        entity="contract",
        entity_id=c.id,
        reason=reason,
    )
    return c
