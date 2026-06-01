"""farm_plan visit frequency + visit days

Revision ID: 20260601_0003
Revises: 20260601_0002
Create Date: 2026-06-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260601_0003"
down_revision = "20260601_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("farm_plan", sa.Column("visit_frequency", sa.String(16), nullable=True))
    op.add_column("farm_plan", sa.Column("visit_days", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("farm_plan", "visit_days")
    op.drop_column("farm_plan", "visit_frequency")
