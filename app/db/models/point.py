"""포인트 정산 원장 — 나눔 경매 낙찰/판매로 인한 포인트 이동 기록.

활동점수(메모·사진·수확)는 여전히 매번 집계하고, 경매로 인한 ± 변동만 이 원장에
쌓는다. 사용자 잔액 = 활동점수(집계) + 원장 합계. 마이그레이션 20260610_0013.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PointLedger(Base):
    __tablename__ = "point_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 부호 있음: +획득(나눔 판매), −소모(낙찰 구매).
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    # 'auction_win'(낙찰자 차감) | 'auction_sale'(나눔자 적립)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("community_post.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
