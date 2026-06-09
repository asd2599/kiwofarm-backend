"""community tables (게시판: 글·사진·댓글·좋아요·나눔신청)

Revision ID: 20260609_0010
Revises: 20260605_0009
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260609_0010"
down_revision = "20260605_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "community_post",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("author_name", sa.String(40), nullable=False),
        sa.Column("post_type", sa.String(16), nullable=False, server_default="show"),
        sa.Column("crop_slug", sa.String(64), nullable=True),
        sa.Column("crop_name", sa.String(255), nullable=True),
        sa.Column(
            "harvest_record_id",
            sa.Integer(),
            sa.ForeignKey("harvest_record.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("share_status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_community_post_device_id", "community_post", ["device_id"])
    op.create_index("ix_community_post_post_type", "community_post", ["post_type"])
    op.create_index("ix_community_post_crop_slug", "community_post", ["crop_slug"])
    op.create_index("ix_community_post_created_at", "community_post", ["created_at"])

    op.create_table(
        "post_image",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("community_post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(512), nullable=True),
        sa.Column("data", sa.LargeBinary(), nullable=True),
        sa.Column("original_name", sa.String(255), nullable=True),
        sa.Column("content_type", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_post_image_post_id", "post_image", ["post_id"])

    op.create_table(
        "post_comment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("community_post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("author_name", sa.String(40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_post_comment_post_id", "post_comment", ["post_id"])

    op.create_table(
        "post_like",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("community_post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("post_id", "device_id", name="uq_post_like_device"),
    )
    op.create_index("ix_post_like_post_id", "post_like", ["post_id"])

    op.create_table(
        "post_share_request",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("community_post.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requester_device_id", sa.String(64), nullable=False),
        sa.Column("requester_name", sa.String(40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_post_share_request_post_id", "post_share_request", ["post_id"])


def downgrade() -> None:
    op.drop_index("ix_post_share_request_post_id", table_name="post_share_request")
    op.drop_table("post_share_request")
    op.drop_index("ix_post_like_post_id", table_name="post_like")
    op.drop_table("post_like")
    op.drop_index("ix_post_comment_post_id", table_name="post_comment")
    op.drop_table("post_comment")
    op.drop_index("ix_post_image_post_id", table_name="post_image")
    op.drop_table("post_image")
    op.drop_index("ix_community_post_created_at", table_name="community_post")
    op.drop_index("ix_community_post_crop_slug", table_name="community_post")
    op.drop_index("ix_community_post_post_type", table_name="community_post")
    op.drop_index("ix_community_post_device_id", table_name="community_post")
    op.drop_table("community_post")
