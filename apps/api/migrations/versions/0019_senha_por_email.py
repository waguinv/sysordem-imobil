"""Senha: redefinição por e-mail, convite e revogação de sessões ao trocar a senha

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-17

- users.password_changed_at: tokens de acesso emitidos ANTES deixam de valer (trocar ou redefinir a senha
  encerra as sessões abertas em outros aparelhos).
- password_tokens: links de uso único ("redefinir" e "convite"); só o SHA-256 do token fica no banco.
  Global, como `users`.
Só adiciona (expand): o código anterior ignora a coluna e a tabela.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "password_tokens",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        # empresa do convite / do pedido (só referência: a tabela é global, sem RLS, como `users`)
        sa.Column("origin_tenant_id", uuid, sa.ForeignKey("tenants.id")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("purpose IN ('redefinir','convite')", name="ck_password_tokens_purpose"),
    )
    op.create_index("ix_password_tokens_user", "password_tokens", ["user_id", "used_at"])
    role = _ident(APP_ROLE)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE (used_at) ON password_tokens TO {role};
          ELSE
            RAISE WARNING 'papel % não existe; permissões não concedidas', '{APP_ROLE}';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("password_tokens")
    op.drop_column("users", "password_changed_at")
