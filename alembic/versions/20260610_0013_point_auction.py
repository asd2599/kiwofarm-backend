"""포인트 원장 + 나눔 경매(입찰)

Revision ID: 20260610_0013
Revises: 20260610_0012
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260610_0013"
down_revision = "20260610_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 포인트 정산 원장 — 잔액 = 활동점수(집계) + 원장 합계.
    op.create_table(
        "point_ledger",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(64), nullable=False, index=True),
        sa.Column("amount", sa.Integer, nullable=False),  # 부호 있음(+획득/−소모)
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column(
            "post_id",
            sa.Integer,
            sa.ForeignKey("community_post.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # 나눔 경매 필드.
    op.add_column(
        "community_post",
        sa.Column("auction_deadline", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "community_post",
        sa.Column(
            "auction_settled", sa.Boolean, server_default="false", nullable=False
        ),
    )
    op.add_column(
        "community_post",
        sa.Column("auction_winner_device", sa.String(64), nullable=True),
    )
    # 입찰.
    op.create_table(
        "share_bid",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "post_id",
            sa.Integer,
            sa.ForeignKey("community_post.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("bidder_device", sa.String(64), nullable=False, index=True),
        sa.Column("bidder_name", sa.String(40), nullable=False),
        sa.Column("amount", sa.Integer, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("share_bid")
    op.drop_column("community_post", "auction_winner_device")
    op.drop_column("community_post", "auction_settled")
    op.drop_column("community_post", "auction_deadline")
    op.drop_table("point_ledger")
