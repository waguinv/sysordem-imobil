"""Verificação em duas etapas (TOTP) dos usuários

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-16

- user_mfa: 1 por usuário (global, como `users`: o usuário pode estar em várias empresas). O segredo TOTP
  fica CIFRADO (AES-256-GCM, mesma chave do cofre de credenciais, finalidade própria) e `last_step`
  impede reusar o mesmo código.
- user_mfa_recovery_codes: códigos de recuperação de uso único, só o hash (Argon2id).
Só cria tabelas (expand): o código anterior não as usa.
"""

import os

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("APP_DB_ROLE", "imobil_app")


def _ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"nome de papel inválido: {name}")
    return f'"{name}"'


def upgrade() -> None:
    uuid = pg.UUID(as_uuid=True)
    now = sa.text("now()")
    op.create_table(
        "user_mfa",
        sa.Column("user_id", uuid, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("secret_sealed", sa.Text),  # ativo
        sa.Column("pending_secret_sealed", sa.Text),  # em ativação (ainda não confirmado com um código)
        sa.Column("enabled_at", sa.DateTime(timezone=True)),
        sa.Column("last_step", sa.BigInteger),  # último intervalo de 30 s aceito (anti-reuso)
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint("(enabled_at IS NULL) = (secret_sealed IS NULL)", name="ck_user_mfa_enabled"),
    )
    op.create_table(
        "user_mfa_recovery_codes",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("user_id", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_hash", sa.Text, nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )
    op.create_index("ix_user_mfa_recovery_user", "user_mfa_recovery_codes", ["user_id", "used_at"])
    role = _ident(APP_ROLE)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE ON user_mfa TO {role};
            GRANT SELECT, INSERT, UPDATE (used_at) ON user_mfa_recovery_codes TO {role};
          ELSE
            RAISE WARNING 'papel % não existe; permissões não concedidas', '{APP_ROLE}';
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_table("user_mfa_recovery_codes")
    op.drop_table("user_mfa")
