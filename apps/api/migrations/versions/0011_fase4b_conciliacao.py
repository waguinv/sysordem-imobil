"""Fase 4b: contas bancárias, extratos importados (OFX/CSV) e conciliação + RLS

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11

- bank_accounts: contas da empresa (sem senha/credencial bancária — só identificação).
- bank_statements: cada arquivo importado (append-only, exceto os contadores preenchidos na própria
  importação; o mesmo arquivo não entra duas vezes).
- statement_lines: lançamentos do extrato, valor com sinal (+ crédito, − débito). Dedup por
  (empresa, conta, id do lançamento no banco) — extratos com períodos sobrepostos não duplicam linhas.
  Append-only, exceto as colunas de "ignorar".
- reconciliations: liga uma linha a recebimentos (créditos) ou a pagamentos de contas a pagar
  (débitos). Nunca é apagada: desfazer marca `undone_*`. Um lançamento só fica conciliado com UMA
  linha ativa (índices únicos parciais).
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")
TENANT_EXPR = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

GRANTS = {
    "bank_accounts": "SELECT, INSERT, UPDATE",
    "bank_statements": "SELECT, INSERT, UPDATE (lines_new, auto_matched)",
    "statement_lines": "SELECT, INSERT, UPDATE (ignored_at, ignored_by, ignored_reason)",
    "reconciliations": "SELECT, INSERT, UPDATE (undone_at, undone_by, undo_reason)",
}


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")
    money = sa.Numeric(14, 2)

    op.create_table(
        "bank_accounts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("bank_name", sa.String(80)),
        sa.Column("branch", sa.String(20)),
        sa.Column("account_number", sa.String(30)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bank_accounts_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_bank_accounts_name"),
    )

    op.create_table(
        "bank_statements",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("bank_account_id", uuid, nullable=False),
        sa.Column("file_name", sa.String(200), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("format", sa.String(10), nullable=False),
        sa.Column("period_start", sa.Date),
        sa.Column("period_end", sa.Date),
        sa.Column("lines_total", sa.Integer, nullable=False),
        sa.Column("lines_new", sa.Integer, nullable=False),
        sa.Column("auto_matched", sa.Integer, nullable=False, server_default="0"),
        sa.Column("imported_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bank_statements_tenant_id"),
        sa.UniqueConstraint("tenant_id", "bank_account_id", "file_sha256", name="uq_bank_statements_file"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bank_account_id"], ["bank_accounts.tenant_id", "bank_accounts.id"]
        ),
        sa.CheckConstraint("format IN ('ofx','csv')", name="ck_bank_statements_format"),
    )

    op.create_table(
        "statement_lines",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("bank_account_id", uuid, nullable=False),
        sa.Column("statement_id", uuid, nullable=False),
        sa.Column("posted_on", sa.Date, nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("fit_id", sa.String(120), nullable=False),
        sa.Column("ignored_at", sa.DateTime(timezone=True)),
        sa.Column("ignored_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("ignored_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("tenant_id", "id", name="uq_statement_lines_tenant_id"),
        sa.UniqueConstraint("tenant_id", "bank_account_id", "fit_id", name="uq_statement_lines_fit"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bank_account_id"], ["bank_accounts.tenant_id", "bank_accounts.id"]
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "statement_id"], ["bank_statements.tenant_id", "bank_statements.id"]
        ),
        sa.CheckConstraint("amount <> 0", name="ck_statement_lines_amount"),
        sa.CheckConstraint(
            "(ignored_at IS NULL) = (ignored_reason IS NULL)", name="ck_statement_lines_ignored"
        ),
    )
    op.create_index(
        "ix_statement_lines_posted", "statement_lines", ["tenant_id", "bank_account_id", "posted_on"]
    )

    op.create_table(
        "reconciliations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", uuid, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("line_id", uuid, nullable=False),
        sa.Column("payment_id", uuid),
        sa.Column("payable_event_id", uuid),
        sa.Column("amount", money, nullable=False),
        sa.Column("auto", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.Column("undone_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("undo_reason", sa.Text),
        sa.ForeignKeyConstraint(
            ["tenant_id", "line_id"], ["statement_lines.tenant_id", "statement_lines.id"]
        ),
        sa.ForeignKeyConstraint(["tenant_id", "payment_id"], ["payments.tenant_id", "payments.id"]),
        sa.CheckConstraint(
            "(payment_id IS NULL) <> (payable_event_id IS NULL)", name="ck_reconciliations_target"
        ),
        sa.CheckConstraint("amount > 0", name="ck_reconciliations_amount"),
        sa.CheckConstraint("(undone_at IS NULL) = (undo_reason IS NULL)", name="ck_reconciliations_undo"),
    )
    # payable_events ainda não tinha (tenant_id, id) único: cria para a FK composta
    op.create_unique_constraint("uq_payable_events_tenant_id", "payable_events", ["tenant_id", "id"])
    op.create_foreign_key(
        "fk_reconciliations_payable_event",
        "reconciliations",
        "payable_events",
        ["tenant_id", "payable_event_id"],
        ["tenant_id", "id"],
    )
    # um lançamento do sistema só pode estar conciliado com UMA linha ativa
    op.create_index(
        "uq_reconciliations_payment",
        "reconciliations",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("undone_at IS NULL AND payment_id IS NOT NULL"),
    )
    op.create_index(
        "uq_reconciliations_payable_event",
        "reconciliations",
        ["payable_event_id"],
        unique=True,
        postgresql_where=sa.text("undone_at IS NULL AND payable_event_id IS NOT NULL"),
    )
    op.create_index("ix_reconciliations_line", "reconciliations", ["line_id"])

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
    for table in ("reconciliations", "statement_lines", "bank_statements", "bank_accounts"):
        op.drop_table(table)
    op.drop_constraint("uq_payable_events_tenant_id", "payable_events", type_="unique")
