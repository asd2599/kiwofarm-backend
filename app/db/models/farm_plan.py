"""영농 캘린더 모델 (계획·작업·메모).

마이그레이션 20260601_0002(farm_plan / farm_task / task_memo) + 20260601_0003
(farm_plan.visit_frequency / visit_days) 과 1:1 매핑.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class FarmPlan(Base):
    __tablename__ = "farm_plan"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 익명 디바이스 ID(X-Device-Id 헤더) — 계정 도입 전 사용자 구분. 'demo'=시연 계정.
    device_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="demo", index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    crop_item_code: Mapped[str] = mapped_column(String(64), nullable=False)
    crop_kind_code: Mapped[str] = mapped_column(String(64), nullable=False)
    crop_name: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str] = mapped_column(String(255), nullable=False)
    province: Mapped[str | None] = mapped_column(String(255), nullable=True)
    area: Mapped[float] = mapped_column(Float, nullable=False)
    area_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    track_progress: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    visit_frequency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    visit_days: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tasks: Mapped[list[FarmTask]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="FarmTask.order",
    )
    memos: Mapped[list[TaskMemo]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="TaskMemo.memo_date",
    )


class FarmTask(Base):
    __tablename__ = "farm_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("farm_plan.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    day_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_days: Mapped[int] = mapped_column(
        Integer, server_default="1", nullable=False
    )
    # "order" 는 SQL 예약어 → 컬럼명을 명시적으로 지정.
    order: Mapped[int] = mapped_column(
        "order", Integer, server_default="0", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), server_default="planned", nullable=False
    )
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    plan: Mapped[FarmPlan] = relationship(back_populates="tasks")


class TaskMemo(Base):
    __tablename__ = "task_memo"
    __table_args__ = (
        UniqueConstraint("plan_id", "memo_date", name="uq_task_memo_plan_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("farm_plan.id", ondelete="CASCADE"), nullable=False, index=True
    )
    memo_date: Mapped[date] = mapped_column(Date, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    plan: Mapped[FarmPlan] = relationship(back_populates="memos")
    images: Mapped[list[MemoImage]] = relationship(
        back_populates="memo",
        cascade="all, delete-orphan",
        order_by="MemoImage.id",
    )


class MemoImage(Base):
    """메모 사진 첨부 — 마이그레이션 20260604_0004 + 20260605_0006 과 1:1 매핑.

    사진 원본은 data(bytea) 에 저장한다(서버 재배포에도 유지). file_path 는
    디스크 저장 시절(/uploads) 레거시 행 전용 — 신규 업로드에서는 NULL.
    """

    __tablename__ = "memo_image"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memo_id: Mapped[int] = mapped_column(
        ForeignKey("task_memo.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # deferred: 캘린더/계획 조회 때 사진 바이트까지 끌어오지 않도록 지연 로딩.
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.now(), nullable=False
    )

    memo: Mapped[TaskMemo] = relationship(back_populates="images")
