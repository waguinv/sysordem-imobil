"""PSP real (Asaas): conta de recebimento de cada empresa, CNPJ da empresa e link de pagamento

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-16

- `payment_accounts`: uma conta de PSP por empresa. A chave de API fica CIFRADA (AES-256-GCM, chave
  fora do banco); o token do webhook só como SHA-256. RLS por empresa.
- `tenants.document`: CNPJ/CPF da empresa (o Asaas da plataforma exige para cobrar a assinatura). A API
  só pode alterar essa coluna (GRANT por coluna).
- `charges.payment_url` / `subscription_invoices.payment_url`: página do PSP com o Pix/boleto.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    op.add_column("tenants", sa.Column("document", sa.String(20)))
    op.create_check_constraint("ck_tenants_document", "tenants", "document ~ '^([0-9]{11}|[0-9]{14})$'")
    op.add_column("charges", sa.Column("payment_url", sa.Text))
    op.add_column("subscription_invoices", sa.Column("payment_url", sa.Text))

    op.create_table(
        "payment_accounts",
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("api_key_sealed", sa.Text, nullable=False),
        sa.Column("api_key_hint", sa.String(12), nullable=False),
        sa.Column("account_name", sa.String(160)),
        sa.Column("webhook_id", sa.String(100)),
        sa.Column("webhook_token_sha256", sa.String(64)),
        sa.Column("webhook_url", sa.Text),
        sa.Column("updated_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("provider IN ('asaas')", name="ck_payment_accounts_provider"),
        sa.CheckConstraint("environment IN ('sandbox','production')", name="ck_payment_accounts_environment"),
        sa.CheckConstraint(
            "webhook_token_sha256 IS NULL OR webhook_token_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_payment_accounts_token",
        ),
    )
    table = "payment_accounts"
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
            GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role};
            GRANT UPDATE (document) ON tenants TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            REVOKE UPDATE (document) ON tenants FROM {_ident(APP_ROLE)};
          END IF;
        END $$;
        """
    )
    op.drop_table("payment_accounts")
    op.drop_column("subscription_invoices", "payment_url")
    op.drop_column("charges", "payment_url")
    op.drop_constraint("ck_tenants_document", "tenants", type_="check")
    op.drop_column("tenants", "document")
