"""Fase 0: tenants, users, memberships, audit_events + RLS

Revision ID: 0001
Revises:
Create Date: 2026-11-10
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Papel usado pela aplicação (sem superuser, sem BYPASSRLS). Criado por docker/db/init ou pelos testes.
APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")

TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"
RLS_TABLES = ("memberships", "audit_events")


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")

    op.create_table(
        "tenants",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("slug", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint("slug = lower(slug)", name="ck_tenants_slug_lower"),
    )
    op.create_table(
        "users",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_table(
        "memberships",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        sa.CheckConstraint(
            "role IN ('owner','admin','financeiro','comercial','corretor','leitura')",
            name="ck_memberships_role",
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("actor_user_id", uuid, sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80)),
        sa.Column("reason", sa.Text),
        sa.Column("data", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )

    # Row-Level Security: FORCE vale até para o dono da tabela (superusers continuam ignorando RLS,
    # por isso a aplicação NUNCA conecta como superuser).
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {TENANT_EXPR}) WITH CHECK (tenant_id = {TENANT_EXPR})"
        )

    role = _ident(APP_ROLE)
    op.execute(  # nome do papel validado por _ident()
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT USAGE ON SCHEMA public TO {role};
            GRANT SELECT ON tenants TO {role};
            GRANT SELECT, INSERT, UPDATE ON users TO {role};
            GRANT SELECT, INSERT, UPDATE ON memberships TO {role};
            -- auditoria append-only: sem UPDATE/DELETE
            GRANT SELECT, INSERT ON audit_events TO {role};
            GRANT SELECT ON alembic_version TO {role};
          ELSE
            RAISE WARNING 'papel % não existe; permissões não concedidas', '{APP_ROLE}';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    for table in ("audit_events", "memberships", "users", "tenants"):
        op.drop_table(table)
