"""Estorno automático: cobrança "estornada" (devolvida ao pagador no PSP)

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-16

Só amplia restrições (expand): o código anterior nunca grava "estornada", então convive com esta
migration durante o deploy. A cobrança estornada continua ligada ao recebimento (que foi estornado).
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

OLD = "'pendente','paga','cancelada','expirada','divergente','resolvida'"
NEW = OLD + ",'estornada'"


def upgrade() -> None:
    op.drop_constraint("ck_charges_status", "charges", type_="check")
    op.create_check_constraint("ck_charges_status", "charges", f"status IN ({NEW})")
    op.drop_constraint("ck_charges_paid", "charges", type_="check")
    op.create_check_constraint(
        "ck_charges_paid", "charges", "(status IN ('paga','estornada')) = (payment_id IS NOT NULL)"
    )


def downgrade() -> None:
    op.execute("UPDATE charges SET status = 'paga' WHERE status = 'estornada'")
    op.drop_constraint("ck_charges_paid", "charges", type_="check")
    op.create_check_constraint("ck_charges_paid", "charges", "(status = 'paga') = (payment_id IS NOT NULL)")
    op.drop_constraint("ck_charges_status", "charges", type_="check")
    op.create_check_constraint("ck_charges_status", "charges", f"status IN ({OLD})")
