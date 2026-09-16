"""confirmed material shortages

Revision ID: a79f31d6c20b
Revises: 44cbb5c5e7e9
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a79f31d6c20b"
down_revision: Union[str, Sequence[str], None] = "44cbb5c5e7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pedidos", sa.Column("status_antes_material", sa.String(40), nullable=True))
    op.add_column("pedidos", sa.Column("quantidade_total_pdf", sa.Numeric(16, 3), nullable=True))
    op.create_table(
        "pendencias_material",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pedido_id", sa.Uuid(), nullable=False),
        sa.Column("produto", sa.String(255), nullable=False),
        sa.Column("quantidade", sa.Numeric(16, 3), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("previsao_solucao", sa.Date(), nullable=True),
        sa.Column("responsavel", sa.String(180), nullable=False),
        sa.Column("status_pendencia", sa.String(30), nullable=False),
        sa.Column("registrado_em", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registrado_por", sa.Uuid(), nullable=True),
        sa.Column("resolvido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolvido_por", sa.Uuid(), nullable=True),
        sa.Column("resolucao_observacao", sa.Text(), nullable=True),
        sa.CheckConstraint("quantidade > 0", name="ck_pendencias_material_quantidade_positiva"),
        sa.ForeignKeyConstraint(["pedido_id"], ["pedidos.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["registrado_por"], ["usuarios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolvido_por"], ["usuarios.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pendencias_material_pedido_id", "pendencias_material", ["pedido_id"])
    op.create_index("ix_pendencias_material_previsao_solucao", "pendencias_material", ["previsao_solucao"])
    op.create_index("ix_pendencias_material_status_pendencia", "pendencias_material", ["status_pendencia"])


def downgrade() -> None:
    op.drop_index("ix_pendencias_material_status_pendencia", table_name="pendencias_material")
    op.drop_index("ix_pendencias_material_previsao_solucao", table_name="pendencias_material")
    op.drop_index("ix_pendencias_material_pedido_id", table_name="pendencias_material")
    op.drop_table("pendencias_material")
    op.drop_column("pedidos", "quantidade_total_pdf")
    op.drop_column("pedidos", "status_antes_material")
