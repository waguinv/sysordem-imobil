"""Fase 5c: planos SaaS, assinatura da empresa, faturas e cadastro self-service + RLS

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-12

- plans: catálogo GLOBAL (sem tenant_id; a aplicação só lê). Preços e limites são HIPÓTESES (decisão 37).
- tenant_subscriptions: 1 por empresa (plano, fim do teste, cancelamento). A SITUAÇÃO (teste, ativa,
  em atraso, suspensa) é calculada a partir das faturas pagas — não fica gravada para não divergir.
- subscription_invoices: faturas mensais da assinatura (pagas por Pix/boleto no PSP da plataforma).
- billing_events: webhooks do PSP da plataforma (append-only, deduplicados por evento).
- tenants: a aplicação passa a poder INSERIR (id, slug, name) — só para o cadastro self-service.
Empresas sem linha em tenant_subscriptions (criadas antes da 5c ou pelo operador) não têm limite nem bloqueio.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

RLS_GRANTS = {
    "tenant_subscriptions": "SELECT, INSERT, UPDATE",
    "subscription_invoices": "SELECT, INSERT, UPDATE",
    "billing_events": "SELECT, INSERT",
}

PLANS = [
    # code, nome, preço mensal, empreendimentos, lotes, usuários ativos, ordem
    ("essencial", "Essencial", "197.00", 1, 300, 3, 1),
    ("profissional", "Profissional", "397.00", 5, 2000, 10, 2),
    ("empresarial", "Empresarial", "797.00", None, None, None, 3),
]


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")
    money = sa.Numeric(14, 2)

    plans = op.create_table(
        "plans",
        sa.Column("code", sa.String(30), primary_key=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("monthly_price", money, nullable=False),
        sa.Column("max_developments", sa.Integer),  # NULL = sem limite
        sa.Column("max_lots", sa.Integer),
        sa.Column("max_users", sa.Integer),
        sa.Column("sort_order", sa.Integer, nullable=False),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.CheckConstraint("monthly_price >= 0", name="ck_plans_price"),
    )
    op.bulk_insert(
        plans,
        [
            {
                "code": c,
                "name": n,
                "monthly_price": p,
                "max_developments": d,
                "max_lots": lots,
                "max_users": u,
                "sort_order": o,
                "is_public": True,
            }
            for c, n, p, d, lots, u, o in PLANS
        ],
    )

    op.create_table(
        "tenant_subscriptions",
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), primary_key=True),
        sa.Column("plan_code", sa.String(30), sa.ForeignKey("plans.code"), nullable=False),
        sa.Column("trial_ends_at", sa.Date, nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )

    op.create_table(
        "subscription_invoices",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("plan_code", sa.String(30), sa.ForeignKey("plans.code"), nullable=False),
        sa.Column("period_start", sa.Date, nullable=False),
        sa.Column("period_end", sa.Date, nullable=False),  # exclusivo
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="aberta"),
        sa.Column("provider", sa.String(30)),
        sa.Column("method", sa.String(20)),
        sa.Column("external_id", sa.String(100)),
        sa.Column("pix_code", sa.Text),
        sa.Column("boleto_line", sa.String(80)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("paid_amount", money),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_subscription_invoices_tenant_id"),
        sa.CheckConstraint(
            "amount > 0 AND period_end > period_start", name="ck_subscription_invoices_values"
        ),
        sa.CheckConstraint("status IN ('aberta','paga','cancelada')", name="ck_subscription_invoices_status"),
        sa.CheckConstraint("(status = 'paga') = (paid_at IS NOT NULL)", name="ck_subscription_invoices_paid"),
    )
    # um período não é faturado duas vezes (exceto faturas canceladas)
    op.create_index(
        "uq_subscription_invoices_period",
        "subscription_invoices",
        ["tenant_id", "period_start"],
        unique=True,
        postgresql_where=sa.text("status <> 'cancelada'"),
    )
    op.create_index(
        "uq_subscription_invoices_external",
        "subscription_invoices",
        ["provider", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.create_table(
        "billing_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("invoice_id", uuid),
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "provider", "event_id", name="uq_billing_events_dedup"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invoice_id"], ["subscription_invoices.tenant_id", "subscription_invoices.id"]
        ),
    )

    role = _ident(APP_ROLE)
    grants = "\n".join(f"GRANT {privs} ON {t} TO {role};" for t, privs in RLS_GRANTS.items())
    grants += f"\nGRANT SELECT ON plans TO {role};\nGRANT INSERT (id, slug, name) ON tenants TO {role};"
    for table in RLS_GRANTS:
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
    role = _ident(APP_ROLE)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            REVOKE INSERT (id, slug, name) ON tenants FROM {role};
          END IF;
        END $$;
        """
    )
    for table in ("billing_events", "subscription_invoices", "tenant_subscriptions", "plans"):
        op.drop_table(table)
