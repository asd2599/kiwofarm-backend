"""보상 API — 작물 도감·뱃지·연속 기록(Streak).

전부 harvest_record / task_memo 파생 집계 (보상 테이블 없음).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DeviceDep
from app.core.rewards.badges import build_badges
from app.core.rewards.collection import build_collection
from app.core.rewards.compare import build_compare
from app.core.rewards.points import build_points
from app.core.rewards.streak import build_streak
from app.db.session import get_session
from app.schemas.rewards import (
    BadgeOut,
    CollectionOut,
    CompareOut,
    PointsOut,
    RewardsSummary,
    StreakOut,
)

router = APIRouter(prefix="/rewards", tags=["rewards"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/collection", response_model=CollectionOut)
async def get_collection(session: SessionDep, device: DeviceDep) -> CollectionOut:
    return CollectionOut(**await build_collection(session, device))


@router.get("/badges", response_model=list[BadgeOut])
async def get_badges(session: SessionDep, device: DeviceDep) -> list[BadgeOut]:
    return [BadgeOut(**b) for b in await build_badges(session, device)]


@router.get("/streak", response_model=StreakOut)
async def get_streak(session: SessionDep, device: DeviceDep) -> StreakOut:
    return StreakOut(**await build_streak(session, device))


@router.get("/points", response_model=PointsOut)
async def get_points(session: SessionDep, device: DeviceDep) -> PointsOut:
    """누적 기록 점수 — 메모·사진·수확 인증 파생 집계."""
    return PointsOut(**await build_points(session, device))


@router.get("/compare", response_model=CompareOut)
async def get_compare(
    session: SessionDep, device: DeviceDep, crop_slug: str | None = None
) -> CompareOut:
    """긍정형 비교 통계 — crop_slug 지정 시 작물 비교 포함."""
    return CompareOut(**await build_compare(session, device, crop_slug))


@router.get("/summary", response_model=RewardsSummary)
async def get_summary(session: SessionDep, device: DeviceDep) -> RewardsSummary:
    """도감 화면용 통합 응답."""
    return RewardsSummary(
        collection=CollectionOut(**await build_collection(session, device)),
        badges=[BadgeOut(**b) for b in await build_badges(session, device)],
        streak=StreakOut(**await build_streak(session, device)),
        points=PointsOut(**await build_points(session, device)),
        compare=CompareOut(**await build_compare(session, device)),
    )
