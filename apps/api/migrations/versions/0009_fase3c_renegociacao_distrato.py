"""Fase 3c: renegociação e distrato + RLS

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-11

- renegotiations: troca o saldo em aberto por um novo cronograma (parcelas antigas → "renegociada");
- rescissions: distrato de contrato assinado (contrato → "distratado", lote → "disponivel"),
  com retenção e devolução ao comprador via conta a pagar (categoria "devolucao").
Ambas append-only.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

GRANTS = {"renegotiations": "SELECT, INSERT", "rescissions": "SELECT, INSERT"}

PAYABLE_CATEGORIES_OLD = "'comissao','proprietario','fornecedor','obra','imposto','marketing','outro'"
PAYABLE_CATEGORIES_NEW = PAYABLE_CATEGORIES_OLD + ",'devolucao'"


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")
    money = sa.Numeric(14, 2)

    # ── contrato: novo status "distratado" (continua com signed_at) ──
    op.drop_constraint("ck_contracts_status", "sales_contracts", type_="check")
    op.create_check_constraint(
        "ck_contracts_status", "sales_contracts", "status IN ('rascunho','assinado','cancelado','distratado')"
    )
    op.drop_constraint("ck_contracts_signed", "sales_contracts", type_="check")
    op.create_check_constraint(
        "ck_contracts_signed",
        "sales_contracts",
        "(status IN ('assinado','distratado')) = (signed_at IS NOT NULL)",
    )
    # lote distratado pode ser vendido de novo
    op.drop_index("uq_contracts_open_lot", table_name="sales_contracts")
    op.create_index(
        "uq_contracts_open_lot",
        "sales_contracts",
        ["lot_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('cancelado','distratado')"),
    )

    # ── parcelas: status "renegociada" e vínculo com a renegociação que as criou ──
    op.drop_constraint("ck_installments_status", "installments", type_="check")
    op.create_check_constraint(
        "ck_installments_status", "installments", "status IN ('aberta','paga','cancelada','renegociada')"
    )
    op.add_column("installments", sa.Column("renegotiation_id", uuid))

    # ── contas a pagar: devolução ao comprador ──
    op.add_column("payables", sa.Column("customer_id", uuid))
    op.create_foreign_key(
        "fk_payables_customer", "payables", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"]
    )
    op.drop_constraint("ck_payables_category", "payables", type_="check")
    op.create_check_constraint("ck_payables_category", "payables", f"category IN ({PAYABLE_CATEGORIES_NEW})")

    op.create_table(
        "renegotiations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("contract_id", uuid, nullable=False),
        sa.Column("old_balance", money, nullable=False),  # soma do em aberto das parcelas trocadas
        sa.Column("late_charges", money, nullable=False),  # multa/mora incorporadas
        sa.Column("discount", money, nullable=False),
        sa.Column("new_balance", money, nullable=False),
        sa.Column("replaced_count", sa.Integer, nullable=False),
        sa.Column("installments_count", sa.Integer, nullable=False),
        sa.Column("interest_monthly_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column("amortization", sa.String(10), nullable=False),
        sa.Column("first_due_date", sa.Date, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_renegotiations_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"], ["sales_contracts.tenant_id", "sales_contracts.id"]
        ),
        sa.CheckConstraint(
            "old_balance > 0 AND late_charges >= 0 AND discount >= 0 AND new_balance > 0 "
            "AND new_balance = old_balance + late_charges - discount AND installments_count > 0",
            name="ck_renegotiations_values",
        ),
    )
    op.create_foreign_key(
        "fk_installments_renegotiation",
        "installments",
        "renegotiations",
        ["tenant_id", "renegotiation_id"],
        ["tenant_id", "id"],
    )

    op.create_table(
        "rescissions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("contract_id", uuid, nullable=False),
        sa.Column(
            "paid_total", money, nullable=False
        ),  # parcelas pagas (sem multa/mora), líquido de estornos
        sa.Column("retention_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("retention_amount", money, nullable=False),
        sa.Column("refund_amount", money, nullable=False),
        sa.Column("payable_id", uuid),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("contract_id", name="uq_rescissions_contract"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"], ["sales_contracts.tenant_id", "sales_contracts.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "payable_id"], ["payables.tenant_id", "payables.id"]),
        sa.CheckConstraint(
            "paid_total >= 0 AND retention_amount >= 0 AND refund_amount >= 0 "
            "AND refund_amount + retention_amount = paid_total AND retention_pct BETWEEN 0 AND 100",
            name="ck_rescissions_values",
        ),
        sa.CheckConstraint("(refund_amount > 0) = (payable_id IS NOT NULL)", name="ck_rescissions_payable"),
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
    op.drop_table("rescissions")
    op.drop_constraint("fk_installments_renegotiation", "installments", type_="foreignkey")
    op.drop_table("renegotiations")
    op.drop_constraint("ck_payables_category", "payables", type_="check")
    op.create_check_constraint("ck_payables_category", "payables", f"category IN ({PAYABLE_CATEGORIES_OLD})")
    op.drop_constraint("fk_payables_customer", "payables", type_="foreignkey")
    op.drop_column("payables", "customer_id")
    op.drop_column("installments", "renegotiation_id")
    op.drop_constraint("ck_installments_status", "installments", type_="check")
    op.create_check_constraint(
        "ck_installments_status", "installments", "status IN ('aberta','paga','cancelada')"
    )
    op.drop_index("uq_contracts_open_lot", table_name="sales_contracts")
    op.create_index(
        "uq_contracts_open_lot",
        "sales_contracts",
        ["lot_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelado'"),
    )
    op.drop_constraint("ck_contracts_signed", "sales_contracts", type_="check")
    op.create_check_constraint(
        "ck_contracts_signed", "sales_contracts", "(status = 'assinado') = (signed_at IS NOT NULL)"
    )
    op.drop_constraint("ck_contracts_status", "sales_contracts", type_="check")
    op.create_check_constraint(
        "ck_contracts_status", "sales_contracts", "status IN ('rascunho','assinado','cancelado')"
    )
