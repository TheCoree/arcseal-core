"""create_characters_table

Revision ID: a1c2d3e4f5a6
Revises: 3068871b0a3d
Create Date: 2026-05-23 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "3068871b0a3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("initial_position_type", sa.String(length=16), nullable=False),
        sa.Column("base_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attack", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ability", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("passive_ability", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_characters_name", "characters", ["name"], unique=False)
    op.create_index("ix_characters_role", "characters", ["role"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_characters_role", table_name="characters")
    op.drop_index("ix_characters_name", table_name="characters")
    op.drop_table("characters")
