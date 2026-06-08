"""farm_plan·harvest_record.device_id — 익명 디바이스 기반 사용자 구분

계정 체계 전 단계: 프론트가 localStorage UUID 를 X-Device-Id 헤더로 보내고,
계획·수확 기록과 그 파생 집계(도감·뱃지·점수·Streak)를 디바이스별로 나눈다.
기존 행은 'demo'(시연 계정) 소유로 백필.

Revision ID: 20260605_0008
Revises: 20260605_0007
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260605_0008"
down_revision = "20260605_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("farm_plan") as batch:
        batch.add_column(
            sa.Column(
                "device_id", sa.String(64), nullable=False, server_default="demo"
            )
        )
    op.create_index("ix_farm_plan_device_id", "farm_plan", ["device_id"])

    with op.batch_alter_table("harvest_record") as batch:
        batch.add_column(
            sa.Column(
                "device_id", sa.String(64), nullable=False, server_default="demo"
            )
        )
    op.create_index("ix_harvest_record_device_id", "harvest_record", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_harvest_record_device_id", table_name="harvest_record")
    with op.batch_alter_table("harvest_record") as batch:
        batch.drop_column("device_id")
    op.drop_index("ix_farm_plan_device_id", table_name="farm_plan")
    with op.batch_alter_table("farm_plan") as batch:
        batch.drop_column("device_id")
