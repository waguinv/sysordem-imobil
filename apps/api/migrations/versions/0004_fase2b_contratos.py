"""Fase 2b: contratos de venda com versões imutáveis + numeração por empresa + RLS

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-10

Garantias no banco:
- um contrato não cancelado por lote (índice único parcial);
- uma reserva gera no máximo um contrato (único em reservation_id);
- versões de condições append-only (sem UPDATE/DELETE para o papel da aplicação).
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

GRANTS = {
    "tenant_counters": "SELECT, INSERT, UPDATE",
    "sales_contracts": "SELECT, INSERT, UPDATE",
    "contract_versions": "SELECT, INSERT",  # append-only
}


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")

    op.create_table(
        "tenant_counters",
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("value", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "sales_contracts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("lot_id", uuid, nullable=False),
        sa.Column("customer_id", uuid, nullable=False),
        sa.Column("broker_id", uuid),
        sa.Column("reservation_id", uuid),
        sa.Column("status", sa.String(20), nullable=False, server_default="rascunho"),
        sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("signed_at", sa.DateTime(timezone=True)),
        sa.Column("signed_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_reason", sa.Text),
        sa.UniqueConstraint("tenant_id", "id", name="uq_contracts_tenant_id"),
        sa.UniqueConstraint("tenant_id", "number", name="uq_contracts_tenant_number"),
        sa.UniqueConstraint("reservation_id", name="uq_contracts_reservation"),
        sa.ForeignKeyConstraint(["tenant_id", "lot_id"], ["lots.tenant_id", "lots.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "broker_id"], ["brokers.tenant_id", "brokers.id"]),
        sa.CheckConstraint("status IN ('rascunho','assinado','cancelado')", name="ck_contracts_status"),
        sa.CheckConstraint("(status = 'assinado') = (signed_at IS NOT NULL)", name="ck_contracts_signed"),
        sa.CheckConstraint(
            "(status = 'cancelado') = (cancelled_at IS NOT NULL)", name="ck_contracts_cancelled"
        ),
    )
    # FK composta para a reserva (a reserva precisa ser do mesmo tenant)
    op.create_unique_constraint("uq_reservations_tenant_id", "reservations", ["tenant_id", "id"])
    op.create_foreign_key(
        "fk_contracts_reservation",
        "sales_contracts",
        "reservations",
        ["tenant_id", "reservation_id"],
        ["tenant_id", "id"],
    )
    op.create_index(
        "uq_contracts_open_lot",
        "sales_contracts",
        ["lot_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelado'"),
    )
    op.create_table(
        "contract_versions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("contract_id", uuid, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("total_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("down_payment", sa.Numeric(14, 2), nullable=False),
        sa.Column("installments_count", sa.Integer, nullable=False),
        sa.Column("installment_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("interest_monthly_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column("first_due_date", sa.Date),
        sa.Column("adjustment_index", sa.String(10), nullable=False),
        sa.Column("late_fee_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("late_interest_monthly_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("contract_id", "version", name="uq_contract_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"], ["sales_contracts.tenant_id", "sales_contracts.id"]
        ),
        sa.CheckConstraint("total_price > 0", name="ck_cv_total"),
        sa.CheckConstraint("down_payment >= 0 AND down_payment <= total_price", name="ck_cv_down"),
        sa.CheckConstraint("installments_count BETWEEN 0 AND 480", name="ck_cv_n"),
        sa.CheckConstraint("(installments_count = 0) = (down_payment = total_price)", name="ck_cv_cash_sale"),
        sa.CheckConstraint("installment_amount >= 0", name="ck_cv_amount"),
        sa.CheckConstraint("interest_monthly_pct >= 0 AND interest_monthly_pct <= 10", name="ck_cv_interest"),
        sa.CheckConstraint("late_fee_pct >= 0 AND late_fee_pct <= 100", name="ck_cv_late_fee"),
        sa.CheckConstraint(
            "late_interest_monthly_pct >= 0 AND late_interest_monthly_pct <= 100", name="ck_cv_late_interest"
        ),
        sa.CheckConstraint("adjustment_index IN ('nenhum','IPCA','IGPM','INCC')", name="ck_cv_index"),
        sa.CheckConstraint("installments_count = 0 OR first_due_date IS NOT NULL", name="ck_cv_first_due"),
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
    op.drop_table("contract_versions")
    op.drop_table("sales_contracts")
    op.drop_constraint("uq_reservations_tenant_id", "reservations")
    op.drop_table("tenant_counters")
