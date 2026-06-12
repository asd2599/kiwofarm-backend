"""자랑글 꾸미기 프리셋(style) 컬럼

Revision ID: 20260612_0014
Revises: 20260610_0013
Create Date: 2026-06-12
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260612_0014"
down_revision = "20260610_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 글 전체 스타일 프리셋 "font|size|align". 기존 행은 null(=기본 프리셋).
    op.add_column(
        "community_post",
        sa.Column("style", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("community_post", "style")
