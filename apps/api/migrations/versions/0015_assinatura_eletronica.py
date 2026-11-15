"""Assinatura eletrônica de contratos (adaptador: Documenso / simulador) + RLS

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-14

- Um envio vale para UMA versão do contrato e guarda o SHA-256 do PDF enviado.
- No máximo um envio em andamento por contrato (índice único parcial).
- Notificações do provedor reutilizam `webhook_events` (dedup por empresa/provedor/evento).
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
ACTIVE = "status IN ('enviando','enviado')"


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    op.create_table(
        "signature_requests",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("contract_id", uuid, nullable=False),
        sa.Column("contract_version", sa.Integer, nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False, server_default="enviando"),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column("signers", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("detail", sa.Text),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id", name="uq_signature_requests_tenant_id"),
        sa.UniqueConstraint("provider", "external_id", name="uq_signature_requests_external"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"], ["sales_contracts.tenant_id", "sales_contracts.id"]
        ),
        sa.CheckConstraint(
            "status IN ('enviando','enviado','assinado','recusado','cancelado','erro')",
            name="ck_signature_requests_status",
        ),
        sa.CheckConstraint("document_sha256 ~ '^[0-9a-f]{64}$'", name="ck_signature_requests_sha"),
        sa.CheckConstraint(f"({ACTIVE}) = (closed_at IS NULL)", name="ck_signature_requests_closed"),
    )
    op.create_index(
        "uq_signature_requests_active_contract",
        "signature_requests",
        ["contract_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    table = "signature_requests"
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
    op.drop_table("signature_requests")
