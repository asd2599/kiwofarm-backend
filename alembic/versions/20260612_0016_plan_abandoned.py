"""farm_plan.abandoned (경작 포기 표시)

Revision ID: 20260612_0016
Revises: 20260612_0015
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260612_0016"
down_revision = "20260612_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "farm_plan",
        sa.Column(
            "abandoned",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("farm_plan", "abandoned")
