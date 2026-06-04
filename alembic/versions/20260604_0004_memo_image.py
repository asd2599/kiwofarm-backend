"""memo_image table (메모 사진 첨부)

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
        "memo_image",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "memo_id",
            sa.Integer(),
            sa.ForeignKey("task_memo.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(512), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=True),
        sa.Column("content_type", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_memo_image_memo_id", "memo_image", ["memo_id"])


def downgrade() -> None:
    op.drop_index("ix_memo_image_memo_id", table_name="memo_image")
    op.drop_table("memo_image")
