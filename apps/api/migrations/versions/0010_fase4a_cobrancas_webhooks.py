"""Fase 4a: cobranças (Pix/boleto) por adaptador de pagamentos e webhooks + RLS

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11

- charges: cobrança emitida num PSP (hoje só o "sandbox") para UMA parcela. Não é lançamento
  financeiro — o dinheiro só entra quando o PSP confirma por webhook e vira um `payments` normal
  (redirect de navegador nunca confirma). No máximo uma cobrança pendente por parcela.
- webhook_events: toda notificação autenticada recebida. Dedup por (empresa, provedor, id do evento).
  Append-only, exceto as colunas de resultado do processamento (GRANT UPDATE só nelas).
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

GRANTS = {
    "charges": "SELECT, INSERT, UPDATE",
    "webhook_events": "SELECT, INSERT, UPDATE (charge_id, processed_at, result, detail)",
}

CHARGE_STATUS = "'pendente','paga','cancelada','expirada','divergente','resolvida'"
WEBHOOK_RESULT = "'recebido','processado','ignorado','divergente'"


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")
    money = sa.Numeric(14, 2)

    op.create_table(
        "charges",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("installment_id", uuid, nullable=False),
        sa.Column("contract_id", uuid, nullable=False),
        sa.Column("customer_id", uuid, nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pendente"),
        sa.Column("pix_code", sa.Text),
        sa.Column("boleto_line", sa.String(80)),
        sa.Column("paid_amount", money),
        sa.Column("paid_on", sa.Date),
        sa.Column("payment_id", uuid),
        sa.Column("status_reason", sa.Text),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_charges_tenant_id"),
        sa.UniqueConstraint("provider", "external_id", name="uq_charges_provider_external"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "installment_id"], ["installments.tenant_id", "installments.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contract_id"], ["sales_contracts.tenant_id", "sales_contracts.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "payment_id"], ["payments.tenant_id", "payments.id"]),
        sa.CheckConstraint(f"status IN ({CHARGE_STATUS})", name="ck_charges_status"),
        sa.CheckConstraint("method IN ('pix','boleto')", name="ck_charges_method"),
        sa.CheckConstraint(
            "amount > 0 AND (paid_amount IS NULL OR paid_amount > 0)", name="ck_charges_values"
        ),
        # paga ⇔ virou recebimento
        sa.CheckConstraint("(status = 'paga') = (payment_id IS NOT NULL)", name="ck_charges_paid"),
    )
    # No máximo uma cobrança pendente por parcela.
    op.create_index(
        "uq_charges_pending_installment",
        "charges",
        ["installment_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pendente'"),
    )
    op.create_index("ix_charges_status", "charges", ["tenant_id", "status", "created_at"])

    op.create_table(
        "webhook_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("external_id", sa.String(100)),
        sa.Column("charge_id", uuid),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.String(20), nullable=False, server_default="recebido"),
        sa.Column("detail", sa.Text),
        sa.UniqueConstraint("tenant_id", "provider", "event_id", name="uq_webhook_events_dedup"),
        sa.ForeignKeyConstraint(["tenant_id", "charge_id"], ["charges.tenant_id", "charges.id"]),
        sa.CheckConstraint(f"result IN ({WEBHOOK_RESULT})", name="ck_webhook_events_result"),
    )
    op.create_index("ix_webhook_events_charge", "webhook_events", ["charge_id"])
    op.create_index("ix_webhook_events_received", "webhook_events", ["tenant_id", "received_at"])

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
    for table in ("webhook_events", "charges"):
        op.drop_table(table)
