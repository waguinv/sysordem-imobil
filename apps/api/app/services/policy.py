"""Política de contratos por empresa.

Cada empresa define seus padrões (juros do financiamento, sistema de amortização, correção, multa e
juros de mora) e limites (juros máximo, nº máximo de parcelas, entrada mínima). Na hora do contrato,
empresa e cliente escolhem as condições dentro desses limites.

Sem política salva, valem os padrões abaixo (hipóteses a validar com jurídico/contábil).
"""

import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import ContractPolicy
from app.services import audit

AMORTIZATIONS = ("price", "sac")


@dataclass(frozen=True)
class Policy:
    default_interest_monthly_pct: Decimal = Decimal("0.0000")
    max_interest_monthly_pct: Decimal = Decimal("2.0000")
    default_amortization: str = "price"
    allowed_amortizations: tuple[str, ...] = ("price", "sac")
    default_adjustment_index: str = "nenhum"
    late_fee_pct: Decimal = Decimal("2.00")
    late_interest_monthly_pct: Decimal = Decimal("1.00")
    max_installments: int = 240
    min_down_payment_pct: Decimal = Decimal("0.00")
    allow_override_late_charges: bool = False
    is_default: bool = True


class PolicyError(ValueError):
    pass


def _from_row(row: ContractPolicy) -> Policy:
    return Policy(
        default_interest_monthly_pct=row.default_interest_monthly_pct,
        max_interest_monthly_pct=row.max_interest_monthly_pct,
        default_amortization=row.default_amortization,
        allowed_amortizations=tuple(a for a in row.allowed_amortizations.split(",") if a),
        default_adjustment_index=row.default_adjustment_index,
        late_fee_pct=row.late_fee_pct,
        late_interest_monthly_pct=row.late_interest_monthly_pct,
        max_installments=row.max_installments,
        min_down_payment_pct=row.min_down_payment_pct,
        allow_override_late_charges=row.allow_override_late_charges,
        is_default=False,
    )


def get_policy(db: Session, tenant_id: uuid.UUID) -> Policy:
    row = db.get(ContractPolicy, tenant_id)
    return _from_row(row) if row else Policy()


def validate_policy(p: Policy) -> None:
    if not p.allowed_amortizations or any(a not in AMORTIZATIONS for a in p.allowed_amortizations):
        raise PolicyError("Escolha ao menos um sistema de amortização válido (Price ou SAC)")
    if p.default_amortization not in p.allowed_amortizations:
        raise PolicyError("O sistema padrão precisa estar entre os permitidos")
    if p.default_interest_monthly_pct > p.max_interest_monthly_pct:
        raise PolicyError("Juros padrão não pode ser maior que o juros máximo")


def save_policy(db: Session, tenant_id: uuid.UUID, p: Policy, *, actor: uuid.UUID, reason: str) -> Policy:
    validate_policy(p)
    before = get_policy(db, tenant_id)
    row = db.get(ContractPolicy, tenant_id, with_for_update=True)
    if row is None:
        row = ContractPolicy(tenant_id=tenant_id)
        db.add(row)
    row.default_interest_monthly_pct = p.default_interest_monthly_pct
    row.max_interest_monthly_pct = p.max_interest_monthly_pct
    row.default_amortization = p.default_amortization
    # ordem canônica, para casar com a restrição do banco
    row.allowed_amortizations = ",".join(a for a in AMORTIZATIONS if a in p.allowed_amortizations)
    row.default_adjustment_index = p.default_adjustment_index
    row.late_fee_pct = p.late_fee_pct
    row.late_interest_monthly_pct = p.late_interest_monthly_pct
    row.max_installments = p.max_installments
    row.min_down_payment_pct = p.min_down_payment_pct
    row.allow_override_late_charges = p.allow_override_late_charges
    row.updated_at = datetime.now(UTC)
    row.updated_by = actor
    db.flush()

    def plain(x: Policy) -> dict[str, str]:
        return {k: str(v) for k, v in asdict(x).items() if k != "is_default"}

    audit.record(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor,
        action="contract_policy.update",
        entity="contract_policy",
        entity_id=tenant_id,
        reason=reason,
        data={"before": plain(before), "after": plain(p)},
    )
    return _from_row(row)
