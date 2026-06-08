"""기록 점수 — 일지 메모·사진·수확 인증에서 파생되는 누적 점수.

도감·뱃지와 같은 패턴: 점수 테이블 없이 매번 집계한다. '+N점' 연출은
저장 직전/직후 합계를 비교(diff)해서 만든다 (api/v1/farmplan 참조).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.farm_plan import FarmPlan, MemoImage, TaskMemo
from app.db.models.harvest import HarvestRecord

POINTS_MEMO = 5  # 날짜별 메모(텍스트) 1건
POINTS_PHOTO = 10  # 일지 사진 1장
POINTS_HARVEST = 50  # 수확 인증 1회


async def build_points(session: AsyncSession, device_id: str) -> dict[str, Any]:
    memo_count = (
        await session.scalar(
            select(func.count())
            .select_from(TaskMemo)
            .join(FarmPlan, TaskMemo.plan_id == FarmPlan.id)
            .where(TaskMemo.content != "", FarmPlan.device_id == device_id)
        )
    ) or 0
    photo_count = (
        await session.scalar(
            select(func.count())
            .select_from(MemoImage)
            .join(TaskMemo, MemoImage.memo_id == TaskMemo.id)
            .join(FarmPlan, TaskMemo.plan_id == FarmPlan.id)
            .where(FarmPlan.device_id == device_id)
        )
    ) or 0
    harvest_count = (
        await session.scalar(
            select(func.count())
            .select_from(HarvestRecord)
            .where(
                HarvestRecord.verified.is_(True),
                HarvestRecord.device_id == device_id,
            )
        )
    ) or 0
    return {
        "total": (
            memo_count * POINTS_MEMO
            + photo_count * POINTS_PHOTO
            + harvest_count * POINTS_HARVEST
        ),
        "memoCount": memo_count,
        "photoCount": photo_count,
        "harvestCount": harvest_count,
    }


async def total_points(session: AsyncSession, device_id: str) -> int:
    return (await build_points(session, device_id))["total"]
