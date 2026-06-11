"""보상 API — 작물 도감·뱃지·연속 기록(Streak).

전부 harvest_record / task_memo 파생 집계 (보상 테이블 없음).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DeviceDep
from app.core.rewards.attendance import AlreadyCheckedIn, build_attendance, check_in
from app.core.rewards.badges import (
    BadgeAlreadyClaimed,
    BadgeNotFound,
    BadgeNotMet,
    build_badges,
    claim_badge,
    sync_crop_rewards,
)
from app.core.rewards.collection import build_collection
from app.core.rewards.compare import build_compare
from app.core.rewards.points import build_points
from app.core.rewards.streak import build_streak
from app.db.session import get_session
from app.schemas.rewards import (
    AttendanceClaimOut,
    AttendanceOut,
    BadgeClaimOut,
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
    await sync_crop_rewards(session, device)  # 작물 레벨업 팜 자동 적립
    return CollectionOut(**await build_collection(session, device))


@router.get("/badges", response_model=list[BadgeOut])
async def get_badges(session: SessionDep, device: DeviceDep) -> list[BadgeOut]:
    """뱃지 도감 — 달성(achieved)/획득(claimed)/획득가능(claimable) 상태 포함. 팜은 claim 으로만."""
    return [BadgeOut(**b) for b in await build_badges(session, device)]


@router.post("/badges/{badge_id}/claim", response_model=BadgeClaimOut)
async def post_badge_claim(
    badge_id: str, session: SessionDep, device: DeviceDep
) -> BadgeClaimOut:
    """뱃지 획득 — 달성한 뱃지의 팜을 적립. 미달성=400, 중복=409, 없음=404."""
    try:
        return BadgeClaimOut(**await claim_badge(session, device, badge_id))
    except BadgeNotFound:
        raise HTTPException(status_code=404, detail="존재하지 않는 뱃지예요.") from None
    except BadgeNotMet:
        raise HTTPException(
            status_code=400, detail="아직 달성하지 못한 뱃지예요."
        ) from None
    except BadgeAlreadyClaimed:
        raise HTTPException(status_code=409, detail="이미 획득한 뱃지예요.") from None


@router.get("/streak", response_model=StreakOut)
async def get_streak(session: SessionDep, device: DeviceDep) -> StreakOut:
    return StreakOut(**await build_streak(session, device))


@router.get("/points", response_model=PointsOut)
async def get_points(session: SessionDep, device: DeviceDep) -> PointsOut:
    """누적 기록 점수 — 메모·사진·수확 인증 파생 집계."""
    return PointsOut(**await build_points(session, device))


@router.get("/attendance", response_model=AttendanceOut)
async def get_attendance(session: SessionDep, device: DeviceDep) -> AttendanceOut:
    """출석 현황 — 연속 출석·오늘 출석 여부·20일 보상표."""
    return AttendanceOut(**await build_attendance(session, device))


@router.post("/attendance/check-in", response_model=AttendanceClaimOut)
async def post_attendance_check_in(
    session: SessionDep, device: DeviceDep
) -> AttendanceClaimOut:
    """오늘 출석 — 연속 일차에 맞는 팜 적립. 하루 1회(중복 시 409)."""
    try:
        return AttendanceClaimOut(**await check_in(session, device))
    except AlreadyCheckedIn:
        raise HTTPException(status_code=409, detail="오늘은 이미 출석했어요.") from None


@router.get("/compare", response_model=CompareOut)
async def get_compare(
    session: SessionDep, device: DeviceDep, crop_slug: str | None = None
) -> CompareOut:
    """긍정형 비교 통계 — crop_slug 지정 시 작물 비교 포함."""
    return CompareOut(**await build_compare(session, device, crop_slug))


@router.get("/summary", response_model=RewardsSummary)
async def get_summary(session: SessionDep, device: DeviceDep) -> RewardsSummary:
    """도감 화면용 통합 응답."""
    await sync_crop_rewards(session, device)  # 작물 레벨업 팜 자동 적립(뱃지 팜은 claim 으로만)
    return RewardsSummary(
        collection=CollectionOut(**await build_collection(session, device)),
        badges=[BadgeOut(**b) for b in await build_badges(session, device)],
        streak=StreakOut(**await build_streak(session, device)),
        points=PointsOut(**await build_points(session, device)),
        attendance=AttendanceOut(**await build_attendance(session, device)),
        compare=CompareOut(**await build_compare(session, device)),
    )
