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
from app.db.models.point import PointLedger

POINTS_MEMO = 5  # 날짜별 메모(텍스트) 1건
POINTS_PHOTO = 10  # 일지 사진 1장
POINTS_HARVEST = 50  # 수확 인증 1회


async def _activity_counts(
    session: AsyncSession, device_id: str
) -> tuple[int, int, int]:
    """기록 활동 집계 — (메모 건수, 사진 장수, 수확 인증 횟수)."""
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
    return memo_count, photo_count, harvest_count


def _activity_points(memo_count: int, photo_count: int, harvest_count: int) -> int:
    return (
        memo_count * POINTS_MEMO
        + photo_count * POINTS_PHOTO
        + harvest_count * POINTS_HARVEST
    )


async def _ledger_sum(session: AsyncSession, device_id: str) -> int:
    # 단일 포인트 풀 — 나눔 경매 정산(낙찰 −/판매 +)을 활동점수에 합산해
    # 도감·순위 점수에도 소모/획득이 반영되게 한다(point_ledger 합계).
    return int(
        (
            await session.scalar(
                select(func.coalesce(func.sum(PointLedger.amount), 0)).where(
                    PointLedger.device_id == device_id
                )
            )
        )
        or 0
    )


async def build_points(session: AsyncSession, device_id: str) -> dict[str, Any]:
    memo_count, photo_count, harvest_count = await _activity_counts(session, device_id)
    ledger = await _ledger_sum(session, device_id)
    return {
        "total": _activity_points(memo_count, photo_count, harvest_count) + ledger,
        "memoCount": memo_count,
        "photoCount": photo_count,
        "harvestCount": harvest_count,
    }


async def total_points(session: AsyncSession, device_id: str) -> int:
    return (await build_points(session, device_id))["total"]


async def total_points_with_ledger(
    session: AsyncSession, device_id: str, ledger_sum: int
) -> int:
    """이미 합산한 원장 잔액(ledger_sum)을 재사용해 활동점수만 추가 조회한다.

    출석 흐름처럼 원장을 한 번 읽어 둔 호출부에서 PointLedger 합계 쿼리를 다시
    날리지 않기 위한 경로. (원격 DB 왕복 1회 절약)
    """
    memo_count, photo_count, harvest_count = await _activity_counts(session, device_id)
    return _activity_points(memo_count, photo_count, harvest_count) + ledger_sum
