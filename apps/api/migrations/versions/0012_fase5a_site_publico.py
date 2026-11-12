"""Fase 5a: site público (configuração por empresa, publicação de empreendimentos) + RLS

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11

- site_settings (1 por empresa): textos e contatos do site, publicado ou não, exibir preços.
- developments: publicação explícita no site (`site_published`) com texto PÚBLICO próprio
  (`site_description`) — a descrição interna nunca vai para o site.
- leads: `consent_at` (consentimento LGPD do formulário do site).
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0012"
down_revision = "0011"
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
    now = sa.text("now()")

    op.create_table(
        "site_settings",
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("published", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("title", sa.String(120)),
        sa.Column("tagline", sa.String(200)),
        sa.Column("about", sa.Text),
        sa.Column("contact_phone", sa.String(30)),
        sa.Column("contact_whatsapp", sa.String(30)),
        sa.Column("contact_email", sa.String(254)),
        sa.Column("show_prices", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("lead_form_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("updated_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )

    op.add_column(
        "developments", sa.Column("site_published", sa.Boolean, nullable=False, server_default=sa.false())
    )
    op.add_column("developments", sa.Column("site_description", sa.Text))
    op.add_column("developments", sa.Column("site_published_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_developments_site",
        "developments",
        "NOT site_published OR (site_description IS NOT NULL AND length(trim(site_description)) > 0)",
    )
    op.add_column("leads", sa.Column("consent_at", sa.DateTime(timezone=True)))

    role = _ident(APP_ROLE)
    op.execute("ALTER TABLE site_settings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE site_settings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON site_settings "
        f"USING (tenant_id = {TENANT_EXPR}) WITH CHECK (tenant_id = {TENANT_EXPR})"
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE ON site_settings TO {role};
          ELSE
            RAISE WARNING 'papel % não existe; permissões não concedidas', '{APP_ROLE}';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_column("leads", "consent_at")
    op.drop_constraint("ck_developments_site", "developments", type_="check")
    for col in ("site_published_at", "site_description", "site_published"):
        op.drop_column("developments", col)
    op.drop_table("site_settings")
