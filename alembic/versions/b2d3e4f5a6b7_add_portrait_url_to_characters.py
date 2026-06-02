"""add_portrait_url_to_characters

Revision ID: b2d3e4f5a6b7
Revises: a1c2d3e4f5a6
Create Date: 2026-05-24 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "a1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "characters",
        sa.Column("portrait_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("characters", "portrait_url")
