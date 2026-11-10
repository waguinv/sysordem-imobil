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
from app.services.finance import installment_amount, money
from app.services.lots import LotStatus, apply_status
from app.services.reservations import expire_due


class ContractError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


class ContractNotFoundError(ContractError):
    pass


@dataclass(frozen=True)
class Terms:
    total_price: Decimal | None
    down_payment: Decimal
    installments_count: int
    interest_monthly_pct: Decimal
    first_due_date: date | None
    adjustment_index: str
    late_fee_pct: Decimal
    late_interest_monthly_pct: Decimal
    notes: str | None


def _validate(t: Terms, total: Decimal) -> Decimal:
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
    return installment_amount(balance, t.interest_monthly_pct, t.installments_count)


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
    amount = _validate(t, total)
    v = ContractVersion(
        tenant_id=c.tenant_id,
        contract_id=c.id,
        version=version,
        total_price=money(total),
        down_payment=money(t.down_payment),
        installments_count=t.installments_count,
        installment_amount=amount,
        interest_monthly_pct=t.interest_monthly_pct,
        first_due_date=t.first_due_date if t.installments_count else None,
        adjustment_index=t.adjustment_index,
        late_fee_pct=t.late_fee_pct,
        late_interest_monthly_pct=t.late_interest_monthly_pct,
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
    _validate(terms, total)  # falha antes de consumir número de contrato
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
