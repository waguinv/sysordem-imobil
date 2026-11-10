"""Fase 2a: compradores, corretores e reservas com expiração + RLS

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10

Garantia no banco contra reserva dupla: índice único parcial em reservations(lot_id) WHERE status='ativa'.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
DOC_CHECK = "document IS NULL OR document ~ '^[0-9]{11}$|^[0-9]{14}$'"

GRANTS = {
    "customers": "SELECT, INSERT, UPDATE",
    "brokers": "SELECT, INSERT, UPDATE",
    "reservations": "SELECT, INSERT, UPDATE",  # sem DELETE: reservas encerram por status
}


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")

    def base_cols() -> list[sa.Column]:
        return [
            sa.Column("id", uuid, primary_key=True),
            sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        ]

    op.create_table(
        "customers",
        *base_cols(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("document", sa.String(20)),
        sa.Column("email", sa.String(254)),
        sa.Column("phone", sa.String(30)),
        sa.Column("city", sa.String(120)),
        sa.Column("state", sa.String(2)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_customers_tenant_id"),
        sa.CheckConstraint(DOC_CHECK, name="ck_customers_doc"),
    )
    op.create_index(
        "uq_customers_tenant_document",
        "customers",
        ["tenant_id", "document"],
        unique=True,
        postgresql_where=sa.text("document IS NOT NULL"),
    )
    op.create_table(
        "brokers",
        *base_cols(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("creci", sa.String(30)),
        sa.Column("document", sa.String(20)),
        sa.Column("email", sa.String(254)),
        sa.Column("phone", sa.String(30)),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_brokers_tenant_id"),
        sa.CheckConstraint(DOC_CHECK, name="ck_brokers_doc"),
    )
    op.create_table(
        "reservations",
        *base_cols(),
        sa.Column("lot_id", uuid, nullable=False),
        sa.Column("customer_id", uuid, nullable=False),
        sa.Column("broker_id", uuid),
        sa.Column("status", sa.String(20), nullable=False, server_default="ativa"),
        sa.Column("price", sa.Numeric(14, 2), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.Text),
        sa.ForeignKeyConstraint(["tenant_id", "lot_id"], ["lots.tenant_id", "lots.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "broker_id"], ["brokers.tenant_id", "brokers.id"]),
        sa.CheckConstraint(
            "status IN ('ativa','convertida','cancelada','expirada')", name="ck_reservations_status"
        ),
        sa.CheckConstraint("price >= 0", name="ck_reservations_price"),
        sa.CheckConstraint("(status = 'ativa') = (closed_at IS NULL)", name="ck_reservations_closed_at"),
    )
    # Uma única reserva ativa por lote — barreira final contra reserva dupla.
    op.create_index(
        "uq_reservations_active_lot",
        "reservations",
        ["lot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ativa'"),
    )
    op.create_index("ix_reservations_due", "reservations", ["tenant_id", "status", "expires_at"])

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
    for table in ("reservations", "brokers", "customers"):
        op.drop_table(table)
