"""memo_image.data — 사진 원본을 DB(bytea)에 저장

신규 업로드는 data 에 바이트를 넣고 file_path 는 NULL. 디스크 저장 시절
레거시 행은 file_path 를 유지한다(/uploads 정적 서빙으로 계속 표시).

Revision ID: 20260605_0006
Revises: 20260604_0005
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260605_0006"
down_revision = "20260604_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch_alter_table: SQLite(로컬 개발)에서도 ALTER COLUMN 이 동작하도록.
    with op.batch_alter_table("memo_image") as batch:
        batch.add_column(sa.Column("data", sa.LargeBinary(), nullable=True))
        batch.alter_column(
            "file_path", existing_type=sa.String(512), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("memo_image") as batch:
        batch.alter_column(
            "file_path", existing_type=sa.String(512), nullable=False
        )
        batch.drop_column("data")
