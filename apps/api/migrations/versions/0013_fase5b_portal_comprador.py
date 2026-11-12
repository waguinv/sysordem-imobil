"""Fase 5b: portal do comprador (contas e convites) + RLS

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-11

- portal_accounts: acesso do comprador (1 por comprador por empresa; e-mail único por empresa).
  Senha só existe depois que o comprador ativa o convite.
- portal_tokens: convites/redefinições de uso único. Guarda só o HASH (sha256) do token; o token
  em claro aparece uma única vez para quem gerou. Append-only, exceto `used_at`.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

GRANTS = {
    "portal_accounts": "SELECT, INSERT, UPDATE",
    "portal_tokens": "SELECT, INSERT, UPDATE (used_at)",
}


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")

    op.create_table(
        "portal_accounts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("customer_id", uuid, nullable=False),
        sa.Column("email", sa.String(254), nullable=False),  # sempre minúsculo
        sa.Column("password_hash", sa.Text),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("invited_at", sa.DateTime(timezone=True)),
        sa.Column("invited_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_portal_accounts_tenant_id"),
        sa.UniqueConstraint("tenant_id", "customer_id", name="uq_portal_accounts_customer"),
        sa.UniqueConstraint("tenant_id", "email", name="uq_portal_accounts_email"),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.CheckConstraint("email = lower(email)", name="ck_portal_accounts_email_lower"),
        sa.CheckConstraint(
            "(activated_at IS NULL) = (password_hash IS NULL)", name="ck_portal_accounts_activated"
        ),
    )

    op.create_table(
        "portal_tokens",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("token_hash", name="uq_portal_tokens_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"], ["portal_accounts.tenant_id", "portal_accounts.id"]
        ),
        sa.CheckConstraint("purpose IN ('convite')", name="ck_portal_tokens_purpose"),
    )
    op.create_index("ix_portal_tokens_account", "portal_tokens", ["account_id", "used_at"])

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
    op.drop_table("portal_tokens")
    op.drop_table("portal_accounts")
