"""harvest_record — 수확 인증 기록

Revision ID: 20260604_0004
Revises: 20260601_0003
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260604_0004"
down_revision = "20260601_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "harvest_record",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("farm_plan.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("crop_slug", sa.String(64), nullable=False),
        sa.Column("crop_name", sa.String(255), nullable=False),
        sa.Column("photo_path", sa.String(512), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("verdict", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("harvested_at", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_harvest_record_crop_slug", "harvest_record", ["crop_slug"])


def downgrade() -> None:
    op.drop_index("ix_harvest_record_crop_slug", table_name="harvest_record")
    op.drop_table("harvest_record")
