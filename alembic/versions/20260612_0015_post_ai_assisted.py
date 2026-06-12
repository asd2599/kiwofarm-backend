"""자랑글 AI 자동작성 사용 표시(ai_assisted) 컬럼

Revision ID: 20260612_0015
Revises: 20260612_0014
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260612_0015"
down_revision = "20260612_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # AI 자동작성 사용 여부. 기존 행은 false.
    op.add_column(
        "community_post",
        sa.Column(
            "ai_assisted", sa.Boolean, server_default="false", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("community_post", "ai_assisted")
