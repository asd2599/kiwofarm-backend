"""app_user 닉네임 + 주소(시·군·구)

Revision ID: 20260610_0012
Revises: 20260609_0011
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260610_0012"
down_revision = "20260609_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("nickname", sa.String(40), nullable=True))
    op.add_column(
        "app_user", sa.Column("address_sigungu", sa.String(60), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("app_user", "address_sigungu")
    op.drop_column("app_user", "nickname")
