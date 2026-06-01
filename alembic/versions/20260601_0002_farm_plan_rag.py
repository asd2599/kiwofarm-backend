"""farm plan + RAG doc chunks (pgvector)

Revision ID: 20260601_0002
Revises: 20260529_0001
Create Date: 2026-06-01
"""

from __future__ import annotations

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op

revision = "20260601_0002"
down_revision = "20260529_0001"
branch_labels = None
depends_on = None

EMBED_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "doc_chunk",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("crop_key", sa.String(64), nullable=False),
        sa.Column("sub_category_code", sa.String(64), nullable=True),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(EMBED_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("crop_key", "source", "chunk_index", name="uq_doc_chunk_src_idx"),
    )
    op.create_index("ix_doc_chunk_crop_key", "doc_chunk", ["crop_key"])

    op.create_table(
        "farm_plan",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("crop_item_code", sa.String(64), nullable=False),
        sa.Column("crop_kind_code", sa.String(64), nullable=False),
        sa.Column("crop_name", sa.String(255), nullable=False),
        sa.Column("region", sa.String(255), nullable=False),
        sa.Column("province", sa.String(255), nullable=True),
        sa.Column("area", sa.Float, nullable=False),
        sa.Column("area_unit", sa.String(16), nullable=False),
        sa.Column("track_progress", sa.Boolean, server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "farm_task",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "plan_id",
            sa.Integer,
            sa.ForeignKey("farm_plan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("day_offset", sa.Integer, nullable=False),
        sa.Column("duration_days", sa.Integer, server_default="1", nullable=False),
        sa.Column("order", sa.Integer, server_default="0", nullable=False),
        sa.Column("status", sa.String(16), server_default="planned", nullable=False),
        sa.Column("actual_date", sa.Date, nullable=True),
        sa.Column("source_note", sa.Text, nullable=True),
    )
    op.create_index("ix_farm_task_plan_id", "farm_task", ["plan_id"])

    op.create_table(
        "task_memo",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "plan_id",
            sa.Integer,
            sa.ForeignKey("farm_plan.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("memo_date", sa.Date, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("plan_id", "memo_date", name="uq_task_memo_plan_date"),
    )
    op.create_index("ix_task_memo_plan_id", "task_memo", ["plan_id"])


def downgrade() -> None:
    op.drop_table("task_memo")
    op.drop_table("farm_task")
    op.drop_table("farm_plan")
    op.drop_table("doc_chunk")
