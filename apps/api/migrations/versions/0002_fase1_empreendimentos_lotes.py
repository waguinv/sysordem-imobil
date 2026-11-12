"""Fase 1: proprietários, empreendimentos, quadras, lotes e histórico de status + RLS

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10

Integridade entre tenants: as chaves estrangeiras são COMPOSTAS (tenant_id, id). Checagens de FK
ignoram RLS no PostgreSQL; sem a chave composta, um registro do tenant A poderia apontar para uma
linha do tenant B cujo id fosse conhecido.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

LOT_STATUSES = "('disponivel','reservado','vendido','bloqueado')"

# tabela -> privilégios do papel da aplicação
GRANTS = {
    "land_owners": "SELECT, INSERT, UPDATE",
    "developments": "SELECT, INSERT, UPDATE",
    "blocks": "SELECT, INSERT, UPDATE, DELETE",
    "lots": "SELECT, INSERT, UPDATE",
    "lot_status_history": "SELECT, INSERT",  # append-only
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
        "land_owners",
        *base_cols(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("document", sa.String(20)),
        sa.Column("email", sa.String(254)),
        sa.Column("phone", sa.String(30)),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_land_owners_tenant_id"),
        sa.CheckConstraint(
            "document IS NULL OR document ~ '^[0-9]{11}$|^[0-9]{14}$'", name="ck_land_owners_doc"
        ),
    )
    op.create_table(
        "developments",
        *base_cols(),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("city", sa.String(120)),
        sa.Column("state", sa.String(2)),
        sa.Column("address", sa.String(240)),
        sa.Column("description", sa.Text),
        sa.Column("land_owner_id", uuid),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_developments_tenant_id"),
        sa.ForeignKeyConstraint(["tenant_id", "land_owner_id"], ["land_owners.tenant_id", "land_owners.id"]),
        sa.CheckConstraint("kind IN ('loteamento','chacaras','condominio')", name="ck_developments_kind"),
        sa.CheckConstraint(
            "status IN ('planejamento','lancamento','em_vendas','encerrado')", name="ck_developments_status"
        ),
    )
    op.create_index("ix_developments_tenant", "developments", ["tenant_id"])
    op.create_table(
        "blocks",
        *base_cols(),
        sa.Column("development_id", uuid, nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_blocks_tenant_id"),
        sa.UniqueConstraint("development_id", "code", name="uq_blocks_dev_code"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "development_id"], ["developments.tenant_id", "developments.id"]
        ),
    )
    op.create_table(
        "lots",
        *base_cols(),
        sa.Column("development_id", uuid, nullable=False),
        sa.Column("block_id", uuid, nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("area_m2", sa.Numeric(12, 2), nullable=False),
        sa.Column("front_m", sa.Numeric(8, 2)),
        sa.Column("price", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="disponivel"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("notes", sa.Text),
        sa.Column("geometry", pg.JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_lots_tenant_id"),
        sa.UniqueConstraint("block_id", "number", name="uq_lots_block_number"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "development_id"], ["developments.tenant_id", "developments.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "block_id"], ["blocks.tenant_id", "blocks.id"]),
        sa.CheckConstraint(f"status IN {LOT_STATUSES}", name="ck_lots_status"),
        sa.CheckConstraint("area_m2 > 0", name="ck_lots_area"),
        sa.CheckConstraint("price >= 0", name="ck_lots_price"),
        sa.CheckConstraint("front_m IS NULL OR front_m > 0", name="ck_lots_front"),
    )
    op.create_index("ix_lots_development", "lots", ["tenant_id", "development_id"])
    op.create_table(
        "lot_status_history",
        *base_cols(),
        sa.Column("lot_id", uuid, nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("actor_user_id", uuid, sa.ForeignKey("users.id")),
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.ForeignKeyConstraint(["tenant_id", "lot_id"], ["lots.tenant_id", "lots.id"]),
        sa.CheckConstraint(f"to_status IN {LOT_STATUSES}", name="ck_lot_history_status"),
    )
    op.create_index("ix_lot_history_lot", "lot_status_history", ["tenant_id", "lot_id", "created_at"])

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
    for table in ("lot_status_history", "lots", "blocks", "developments", "land_owners"):
        op.drop_table(table)
