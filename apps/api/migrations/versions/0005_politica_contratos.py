"""Política de contratos por empresa + sistema de amortização (Price/SAC) por versão de contrato

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    op.add_column(
        "contract_versions",
        sa.Column("amortization", sa.String(10), nullable=False, server_default="price"),
    )
    op.create_check_constraint("ck_cv_amortization", "contract_versions", "amortization IN ('price','sac')")

    op.create_table(
        "contract_policies",
        sa.Column("tenant_id", pg.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("default_interest_monthly_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column("max_interest_monthly_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column("default_amortization", sa.String(10), nullable=False),
        sa.Column("allowed_amortizations", sa.String(40), nullable=False),
        sa.Column("default_adjustment_index", sa.String(10), nullable=False),
        sa.Column("late_fee_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("late_interest_monthly_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("max_installments", sa.Integer, nullable=False),
        sa.Column("min_down_payment_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("allow_override_late_charges", sa.Boolean, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.CheckConstraint(
            "default_interest_monthly_pct >= 0 AND default_interest_monthly_pct <= max_interest_monthly_pct "
            "AND max_interest_monthly_pct <= 10",
            name="ck_cp_interest",
        ),
        sa.CheckConstraint("default_amortization IN ('price','sac')", name="ck_cp_default_amort"),
        sa.CheckConstraint(
            "allowed_amortizations IN ('price','sac','price,sac') "
            "AND position(default_amortization in allowed_amortizations) > 0",
            name="ck_cp_allowed_amort",
        ),
        sa.CheckConstraint("default_adjustment_index IN ('nenhum','IPCA','IGPM','INCC')", name="ck_cp_index"),
        sa.CheckConstraint("late_fee_pct BETWEEN 0 AND 10", name="ck_cp_late_fee"),
        sa.CheckConstraint("late_interest_monthly_pct BETWEEN 0 AND 10", name="ck_cp_late_interest"),
        sa.CheckConstraint("max_installments BETWEEN 0 AND 480", name="ck_cp_max_n"),
        sa.CheckConstraint("min_down_payment_pct BETWEEN 0 AND 100", name="ck_cp_min_down"),
    )
    table = "contract_policies"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (tenant_id = {TENANT_EXPR}) WITH CHECK (tenant_id = {TENANT_EXPR})"
    )
    role = _ident(APP_ROLE)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE ON {table} TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("contract_policies")
    op.drop_constraint("ck_cv_amortization", "contract_versions")
    op.drop_column("contract_versions", "amortization")
