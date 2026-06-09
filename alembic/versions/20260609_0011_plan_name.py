"""farm_plan.name (텃밭 고유 이름)

Revision ID: 20260609_0011
Revises: 20260609_0010
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260609_0011"
down_revision = "20260609_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("farm_plan", sa.Column("name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("farm_plan", "name")
