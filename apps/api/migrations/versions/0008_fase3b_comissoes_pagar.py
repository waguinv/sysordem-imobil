"""Fase 3b: comissões, participação do proprietário da área e contas a pagar + RLS

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-11

- finance_settings (1 por empresa): base/momento da comissão e % padrão;
- brokers.commission_pct e developments.owner_share_pct; snapshot no contrato ao assinar;
- participations: créditos de comissão/repasse (positivos) e estornos (negativos). Append-only,
  exceto a coluna payable_id (GRANT UPDATE só nela), que liga o crédito à conta a pagar;
- payables + payable_events (append-only): contas a pagar e seu histórico.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

GRANTS = {
    "finance_settings": "SELECT, INSERT, UPDATE",
    "participations": "SELECT, INSERT, UPDATE (payable_id)",
    "payables": "SELECT, INSERT, UPDATE",
    "payable_events": "SELECT, INSERT",
}

PCT = sa.Numeric(5, 2)


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")
    money = sa.Numeric(14, 2)

    op.add_column("brokers", sa.Column("commission_pct", PCT))
    op.create_check_constraint(
        "ck_brokers_commission", "brokers", "commission_pct IS NULL OR commission_pct BETWEEN 0 AND 100"
    )
    op.add_column("developments", sa.Column("owner_share_pct", PCT, nullable=False, server_default="0"))
    op.create_check_constraint(
        "ck_developments_owner_share", "developments", "owner_share_pct BETWEEN 0 AND 100"
    )
    # Snapshot das regras no momento da assinatura (mudanças posteriores não afetam o contrato).
    op.add_column("sales_contracts", sa.Column("commission_pct", PCT))
    op.add_column("sales_contracts", sa.Column("commission_basis", sa.String(20)))
    op.add_column("sales_contracts", sa.Column("owner_share_pct", PCT))
    op.add_column("sales_contracts", sa.Column("land_owner_id", uuid))

    op.create_table(
        "finance_settings",
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("commission_basis", sa.String(20), nullable=False),
        sa.Column("default_commission_pct", PCT, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by", uuid, sa.ForeignKey("users.id")),
        sa.CheckConstraint(
            "commission_basis IN ('assinatura','recebimento')", name="ck_finance_settings_basis"
        ),
        sa.CheckConstraint("default_commission_pct BETWEEN 0 AND 100", name="ck_finance_settings_pct"),
    )

    op.create_table(
        "payables",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("payee_name", sa.String(160), nullable=False),
        sa.Column("broker_id", uuid),
        sa.Column("land_owner_id", uuid),
        sa.Column("development_id", uuid),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="aberta"),
        sa.Column("paid_on", sa.Date),
        sa.Column("method", sa.String(20)),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_payables_tenant_id"),
        sa.ForeignKeyConstraint(["tenant_id", "broker_id"], ["brokers.tenant_id", "brokers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "land_owner_id"], ["land_owners.tenant_id", "land_owners.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "development_id"], ["developments.tenant_id", "developments.id"]
        ),
        sa.CheckConstraint("amount > 0", name="ck_payables_amount"),
        sa.CheckConstraint("status IN ('aberta','paga','cancelada')", name="ck_payables_status"),
        sa.CheckConstraint(
            "category IN ('comissao','proprietario','fornecedor','obra','imposto','marketing','outro')",
            name="ck_payables_category",
        ),
        sa.CheckConstraint("(status = 'paga') = (paid_on IS NOT NULL)", name="ck_payables_paid"),
    )
    op.create_index("ix_payables_due", "payables", ["tenant_id", "status", "due_date"])

    op.create_table(
        "payable_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("payable_id", uuid, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("event_date", sa.Date),
        sa.Column("method", sa.String(20)),
        sa.Column("reason", sa.Text),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "payable_id"], ["payables.tenant_id", "payables.id"]),
        sa.CheckConstraint(
            "kind IN ('criada','pagamento','estorno','cancelamento')", name="ck_payable_events_kind"
        ),
    )
    op.create_index("ix_payable_events_payable", "payable_events", ["payable_id", "seq"])

    op.create_table(
        "participations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("broker_id", uuid),
        sa.Column("land_owner_id", uuid),
        sa.Column("contract_id", uuid, nullable=False),
        sa.Column("payment_id", uuid),
        sa.Column("reverses_id", uuid),
        sa.Column("base", money, nullable=False),
        sa.Column("pct", PCT, nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("payable_id", uuid),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_participations_tenant_id"),
        sa.ForeignKeyConstraint(["tenant_id", "broker_id"], ["brokers.tenant_id", "brokers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "land_owner_id"], ["land_owners.tenant_id", "land_owners.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"], ["sales_contracts.tenant_id", "sales_contracts.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "payment_id"], ["payments.tenant_id", "payments.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reverses_id"], ["participations.tenant_id", "participations.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "payable_id"], ["payables.tenant_id", "payables.id"]),
        sa.CheckConstraint("kind IN ('comissao','proprietario')", name="ck_participations_kind"),
        sa.CheckConstraint(
            "(kind = 'comissao' AND broker_id IS NOT NULL AND land_owner_id IS NULL) OR "
            "(kind = 'proprietario' AND land_owner_id IS NOT NULL AND broker_id IS NULL)",
            name="ck_participations_beneficiary",
        ),
        sa.CheckConstraint("pct BETWEEN 0 AND 100", name="ck_participations_pct"),
    )
    # Um crédito é estornado no máximo uma vez.
    op.create_index(
        "uq_participations_reverses",
        "participations",
        ["reverses_id"],
        unique=True,
        postgresql_where=sa.text("reverses_id IS NOT NULL"),
    )
    op.create_index("ix_participations_pending", "participations", ["tenant_id", "kind", "payable_id"])

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
    for table in ("participations", "payable_events", "payables", "finance_settings"):
        op.drop_table(table)
    for col in ("land_owner_id", "owner_share_pct", "commission_basis", "commission_pct"):
        op.drop_column("sales_contracts", col)
    op.drop_constraint("ck_developments_owner_share", "developments", type_="check")
    op.drop_column("developments", "owner_share_pct")
    op.drop_constraint("ck_brokers_commission", "brokers", type_="check")
    op.drop_column("brokers", "commission_pct")
