"""Fase 3a: parcelas (cronograma), recebimentos e estornos + RLS

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11

- installments: cronograma gerado na assinatura (nº 0 = entrada); `paid_principal` acompanha o quanto
  da parcela já foi quitado (atualizado sob trava de linha);
- payments e payment_reversals: append-only (o papel da aplicação só tem SELECT, INSERT).
  Lançamento financeiro nunca é apagado nem editado: corrige-se por estorno.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

GRANTS = {
    "installments": "SELECT, INSERT, UPDATE",
    "payments": "SELECT, INSERT",
    "payment_reversals": "SELECT, INSERT",
}


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")
    money = sa.Numeric(14, 2)

    op.create_table(
        "installments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("contract_id", uuid, nullable=False),
        sa.Column("contract_version", sa.Integer, nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("principal", money, nullable=False),
        sa.Column("interest", money, nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("paid_principal", money, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="aberta"),
        sa.Column("paid_at", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_installments_tenant_id"),
        sa.UniqueConstraint("contract_id", "number", name="uq_installments_contract_number"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"], ["sales_contracts.tenant_id", "sales_contracts.id"]
        ),
        sa.CheckConstraint("status IN ('aberta','paga','cancelada')", name="ck_installments_status"),
        sa.CheckConstraint(
            "number >= 0 AND principal >= 0 AND interest >= 0 AND amount = principal + interest",
            name="ck_installments_values",
        ),
        # "pago" acompanha o valor da parcela (encargos de atraso ficam no recebimento)
        sa.CheckConstraint("paid_principal >= 0 AND paid_principal <= amount", name="ck_installments_paid"),
        sa.CheckConstraint(
            "(status = 'paga') = (paid_principal = amount)", name="ck_installments_paid_status"
        ),
    )
    op.create_index("ix_installments_due", "installments", ["tenant_id", "status", "due_date"])

    op.create_table(
        "payments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("installment_id", uuid, nullable=False),
        sa.Column("contract_id", uuid, nullable=False),
        sa.Column("received_on", sa.Date, nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("principal", money, nullable=False),
        sa.Column("late_fee", money, nullable=False),
        sa.Column("late_interest", money, nullable=False),
        sa.Column("days_late", sa.Integer, nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_payments_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "installment_id"], ["installments.tenant_id", "installments.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"], ["sales_contracts.tenant_id", "sales_contracts.id"]
        ),
        sa.CheckConstraint(
            "amount > 0 AND principal >= 0 AND late_fee >= 0 AND late_interest >= 0 "
            "AND amount = principal + late_fee + late_interest AND days_late >= 0",
            name="ck_payments_values",
        ),
        sa.CheckConstraint(
            "method IN ('pix','boleto','transferencia','dinheiro','cartao','cheque','outro')",
            name="ck_payments_method",
        ),
    )
    op.create_index("ix_payments_installment", "payments", ["installment_id"])
    op.create_index("ix_payments_received", "payments", ["tenant_id", "received_on"])

    op.create_table(
        "payment_reversals",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("payment_id", uuid, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("payment_id", name="uq_payment_reversals_payment"),  # estorno único
        sa.ForeignKeyConstraint(["tenant_id", "payment_id"], ["payments.tenant_id", "payments.id"]),
    )

    role = _ident(APP_ROLE)
    grants = "\n".join(f"GRANT {privs} ON {t} TO {role};" for t, privs in GRANTS.items())
    for table in GRANTS:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {TENANT_EXPR}) WITH CHECK (tenant_id = {TENANT_EXPR})"
        )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            {grants}
          ELSE
            RAISE WARNING 'papel % não existe; permissões não concedidas', '{APP_ROLE}';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in ("payment_reversals", "payments", "installments"):
        op.drop_table(table)
