"""harvest_record.photo_path nullable — 일지(메모·사진 누적) 기반 수확 인증

일지 인증은 단일 업로드 사진이 없다. 원본은 memo_image 에 있고
verdict JSON 의 image_ids 로 참조한다.

Revision ID: 20260605_0007
Revises: 20260605_0006
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260605_0007"
down_revision = "20260605_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("harvest_record") as batch:
        batch.alter_column(
            "photo_path", existing_type=sa.String(512), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("harvest_record") as batch:
        batch.alter_column(
            "photo_path", existing_type=sa.String(512), nullable=False
        )
