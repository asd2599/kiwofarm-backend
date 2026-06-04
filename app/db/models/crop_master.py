"""농사로 cropEbook 작목 마스터 트리 (대→중→소→길잡이→목차).

마이그레이션 20260529_0001 의 5개 테이블과 1:1 매핑.
scripts/sync_crop_master.py 가 농사로 API 결과를 이 모델로 upsert 한다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CropMainCategory(Base):
    __tablename__ = "crop_main_category"

    main_category_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    main_category_nm: Mapped[str] = mapped_column(String(255), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CropMiddleCategory(Base):
    __tablename__ = "crop_middle_category"

    middle_category_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    main_category_code: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("crop_main_category.main_category_code", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    middle_category_nm: Mapped[str] = mapped_column(String(255), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CropSubCategory(Base):
    __tablename__ = "crop_sub_category"

    sub_category_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    middle_category_code: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("crop_middle_category.middle_category_code", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sub_category_nm: Mapped[str] = mapped_column(String(255), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CropEbook(Base):
    __tablename__ = "crop_ebook"

    ebook_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    crops_ebook_file_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    sub_category_code: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("crop_sub_category.sub_category_code", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ebook_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    std_item_cd: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    std_item_nm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    orginl_file_nm: Mapped[str | None] = mapped_column(String(512), nullable=True)
    crops_ebook_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    atchmnfl_group_esntl_ebook_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    atchmnfl_group_esntl_ebook_nm: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CropEbookIndex(Base):
    __tablename__ = "crop_ebook_index"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ebook_code", "crops_ebook_file_no"],
            ["crop_ebook.ebook_code", "crop_ebook.crops_ebook_file_no"],
            ondelete="CASCADE",
            name="fk_ebook_index_ebook",
        ),
        UniqueConstraint(
            "ebook_code", "crops_ebook_file_no", "index_sid", name="uq_ebook_index_sid"
        ),
    )

    ebook_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    crops_ebook_file_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    crops_ebook_index_no: Mapped[str] = mapped_column(String(64), primary_key=True)
    ebook_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ebook_mobile_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    index_base_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    index_page: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    index_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    index_name: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    index_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    index_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    std_item_cd: Mapped[str | None] = mapped_column(String(64), nullable=True)
    std_item_nm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
