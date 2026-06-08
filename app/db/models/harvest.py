"""수확 인증 기록 모델.

수확 사진 → AI 멀티모달 검증 → 인증 결과를 보존한다. 뱃지·도감·Streak 는
이 테이블을 원천으로 집계한다. 계정 체계 도입 전까지 farm_plan 에 귀속
(plan_id nullable — 계획 없이 인증도 허용).
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
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HarvestRecord(Base):
    __tablename__ = "harvest_record"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 익명 디바이스 ID — farm_plan.device_id 와 동일 체계. 'demo'=시연 계정.
    device_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="demo", index=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("farm_plan.id", ondelete="SET NULL"), nullable=True
    )
    crop_slug: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    crop_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 단일 사진 인증의 업로드 경로. 일지(메모·사진 누적) 기반 인증은 NULL —
    # 원본 사진은 memo_image 에 있고 verdict.image_ids 로 참조한다.
    photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # AI 판정 원문
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    harvested_at: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
