"""crop master tree (nongsaro cropEbook).

Revision ID: 20260529_0001
Revises:
Create Date: 2026-05-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260529_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crop_main_category",
        sa.Column("main_category_code", sa.String(64), primary_key=True),
        sa.Column("main_category_nm", sa.String(255), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "crop_middle_category",
        sa.Column("middle_category_code", sa.String(64), primary_key=True),
        sa.Column(
            "main_category_code",
            sa.String(64),
            sa.ForeignKey("crop_main_category.main_category_code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("middle_category_nm", sa.String(255), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_crop_middle_category_main_category_code",
        "crop_middle_category",
        ["main_category_code"],
    )

    op.create_table(
        "crop_sub_category",
        sa.Column("sub_category_code", sa.String(64), primary_key=True),
        sa.Column(
            "middle_category_code",
            sa.String(64),
            sa.ForeignKey("crop_middle_category.middle_category_code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sub_category_nm", sa.String(255), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_crop_sub_category_middle_category_code",
        "crop_sub_category",
        ["middle_category_code"],
    )

    op.create_table(
        "crop_ebook",
        sa.Column("ebook_code", sa.String(64), primary_key=True),
        sa.Column("crops_ebook_file_no", sa.String(64), primary_key=True),
        sa.Column(
            "sub_category_code",
            sa.String(64),
            sa.ForeignKey("crop_sub_category.sub_category_code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ebook_name", sa.String(512), nullable=True),
        sa.Column("std_item_cd", sa.String(64), nullable=True),
        sa.Column("std_item_nm", sa.String(255), nullable=True),
        sa.Column("orginl_file_nm", sa.String(512), nullable=True),
        sa.Column("crops_ebook_file", sa.Text, nullable=True),
        sa.Column("atchmnfl_group_esntl_ebook_code", sa.String(64), nullable=True),
        sa.Column("atchmnfl_group_esntl_ebook_nm", sa.String(255), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_crop_ebook_sub_category_code", "crop_ebook", ["sub_category_code"])
    op.create_index("ix_crop_ebook_std_item_cd", "crop_ebook", ["std_item_cd"])

    op.create_table(
        "crop_ebook_index",
        sa.Column("ebook_code", sa.String(64), primary_key=True),
        sa.Column("crops_ebook_file_no", sa.String(64), primary_key=True),
        sa.Column("crops_ebook_index_no", sa.String(64), primary_key=True),
        sa.Column("ebook_url", sa.Text, nullable=True),
        sa.Column("ebook_mobile_url", sa.Text, nullable=True),
        sa.Column("index_base_page", sa.Integer, nullable=True),
        sa.Column("index_page", sa.Integer, nullable=True),
        sa.Column("index_level", sa.Integer, nullable=True),
        sa.Column("index_name", sa.String(512), nullable=True),
        sa.Column("index_order", sa.Integer, nullable=True),
        sa.Column("index_sid", sa.String(64), nullable=True),
        sa.Column("std_item_cd", sa.String(64), nullable=True),
        sa.Column("std_item_nm", sa.String(255), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ebook_code", "crops_ebook_file_no"],
            ["crop_ebook.ebook_code", "crop_ebook.crops_ebook_file_no"],
            ondelete="CASCADE",
            name="fk_ebook_index_ebook",
        ),
        sa.UniqueConstraint(
            "ebook_code", "crops_ebook_file_no", "index_sid", name="uq_ebook_index_sid"
        ),
    )
    op.create_index("ix_crop_ebook_index_index_name", "crop_ebook_index", ["index_name"])
    op.create_index("ix_crop_ebook_index_index_page", "crop_ebook_index", ["index_page"])


def downgrade() -> None:
    op.drop_table("crop_ebook_index")
    op.drop_table("crop_ebook")
    op.drop_table("crop_sub_category")
    op.drop_table("crop_middle_category")
    op.drop_table("crop_main_category")
