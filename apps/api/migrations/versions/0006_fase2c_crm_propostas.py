"""Fase 2c: leads (CRM), atividades, propostas e escopo do corretor + RLS

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10

- leads e lead_activities (append-only: sem UPDATE/DELETE para o papel da aplicação);
- proposals: condições negociadas; aceitar reserva o lote (proposals.reservation_id);
- customers.broker_id: corretor responsável pelo cliente (escopo "só meus clientes");
- brokers.user_id único por empresa: liga o corretor ao usuário que acessa o sistema.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

GRANTS = {
    "leads": "SELECT, INSERT, UPDATE",
    "lead_activities": "SELECT, INSERT",  # histórico imutável
    "proposals": "SELECT, INSERT, UPDATE",  # encerram por status, nunca são apagadas
}


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")

    # ── escopo do corretor ──
    op.add_column("customers", sa.Column("broker_id", uuid))
    op.create_foreign_key(
        "fk_customers_broker", "customers", "brokers", ["tenant_id", "broker_id"], ["tenant_id", "id"]
    )
    op.create_index(
        "uq_brokers_tenant_user",
        "brokers",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )

    # ── leads ──
    op.create_table(
        "leads",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("email", sa.String(254)),
        sa.Column("phone", sa.String(30)),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("development_id", uuid),
        sa.Column("broker_id", uuid),
        sa.Column("status", sa.String(20), nullable=False, server_default="novo"),
        sa.Column("customer_id", uuid),
        sa.Column("lost_reason", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_leads_tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "development_id"], ["developments.tenant_id", "developments.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "broker_id"], ["brokers.tenant_id", "brokers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.CheckConstraint(
            "status IN ('novo','em_contato','qualificado','convertido','perdido')", name="ck_leads_status"
        ),
        sa.CheckConstraint(
            "source IN ('site','indicacao','whatsapp','telefone','visita','redes_sociais','outro')",
            name="ck_leads_source",
        ),
        sa.CheckConstraint("(status = 'convertido') = (customer_id IS NOT NULL)", name="ck_leads_converted"),
        sa.CheckConstraint("status <> 'perdido' OR lost_reason IS NOT NULL", name="ck_leads_lost_reason"),
    )
    op.create_index("ix_leads_tenant_status", "leads", ["tenant_id", "status", "created_at"])
    op.create_index("ix_leads_tenant_broker", "leads", ["tenant_id", "broker_id"])

    op.create_table(
        "lead_activities",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("lead_id", uuid, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        # ordem estável dentro da mesma transação (created_at = now() é igual para todas)
        sa.Column("seq", sa.BigInteger, sa.Identity(always=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id", "lead_id"], ["leads.tenant_id", "leads.id"]),
        sa.CheckConstraint(
            "kind IN ('nota','ligacao','whatsapp','email','visita','status')", name="ck_lead_activities_kind"
        ),
    )
    op.create_index("ix_lead_activities_lead", "lead_activities", ["lead_id", "seq"])

    # ── propostas ──
    op.create_table(
        "proposals",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("lot_id", uuid, nullable=False),
        sa.Column("customer_id", uuid, nullable=False),
        sa.Column("broker_id", uuid),
        sa.Column("lead_id", uuid),
        sa.Column("status", sa.String(20), nullable=False, server_default="aberta"),
        sa.Column("valid_until", sa.Date, nullable=False),
        sa.Column("total_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("down_payment", sa.Numeric(14, 2), nullable=False),
        sa.Column("installments_count", sa.Integer, nullable=False),
        sa.Column("installment_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("amortization", sa.String(10), nullable=False),
        sa.Column("interest_monthly_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column("first_due_date", sa.Date),
        sa.Column("notes", sa.Text),
        sa.Column("reservation_id", uuid),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("decision_reason", sa.Text),
        sa.ForeignKeyConstraint(["tenant_id", "lot_id"], ["lots.tenant_id", "lots.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "broker_id"], ["brokers.tenant_id", "brokers.id"]),
        sa.ForeignKeyConstraint(["tenant_id", "lead_id"], ["leads.tenant_id", "leads.id"]),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reservation_id"], ["reservations.tenant_id", "reservations.id"]
        ),
        sa.CheckConstraint(
            "status IN ('aberta','aceita','recusada','cancelada','expirada')", name="ck_proposals_status"
        ),
        sa.CheckConstraint(
            "total_price > 0 AND down_payment >= 0 AND down_payment <= total_price "
            "AND installments_count >= 0 AND installment_amount >= 0",
            name="ck_proposals_values",
        ),
        sa.CheckConstraint("amortization IN ('price','sac')", name="ck_proposals_amortization"),
        sa.CheckConstraint(
            "(status = 'aceita') = (reservation_id IS NOT NULL)", name="ck_proposals_accepted"
        ),
        sa.CheckConstraint("(status = 'aberta') = (decided_at IS NULL)", name="ck_proposals_decided"),
    )
    op.create_index("ix_proposals_tenant_status", "proposals", ["tenant_id", "status", "created_at"])
    op.create_index("ix_proposals_lot", "proposals", ["lot_id"])
    # Uma reserva nasce de no máximo uma proposta.
    op.create_index(
        "uq_proposals_reservation",
        "proposals",
        ["reservation_id"],
        unique=True,
        postgresql_where=sa.text("reservation_id IS NOT NULL"),
    )

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
    for table in ("proposals", "lead_activities", "leads"):
        op.drop_table(table)
    op.drop_index("uq_brokers_tenant_user", table_name="brokers")
    op.drop_constraint("fk_customers_broker", "customers", type_="foreignkey")
    op.drop_column("customers", "broker_id")
