"""app_user — 아이디+비밀번호 자체 인증

Revision ID: 20260605_0009
Revises: 20260605_0008
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260605_0009"
down_revision = "20260605_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(32), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_app_user_username", "app_user", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_app_user_username", table_name="app_user")
    op.drop_table("app_user")
