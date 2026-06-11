"""출석 보상 — PointLedger(reason='attendance') 파생. 별도 테이블 없음.

연속 출석 20일 사이클(미스 시 1일차 리셋, 20일 채우면 1일차로 반복). 하루 1회.
일차 보상: 기본 10, 5일=20, 10일=50, 15일=20, 20일=100.
출석일 = 'attendance' 원장 row 의 KST 날짜 집합으로 판정(streak.py 와 동일 철학).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import KST, kst_today
from app.core.rewards.points import total_points
from app.db.models.point import PointLedger

ATTENDANCE_REASON = "attendance"
CYCLE_DAYS = 20
BASE_REWARD = 10
_MILESTONES = {5: 20, 10: 50, 15: 20, 20: 100}


class AlreadyCheckedIn(Exception):
    """오늘 이미 출석함."""


def reward_for_day(cycle_day: int) -> int:
    return _MILESTONES.get(cycle_day, BASE_REWARD)


def _cycle_day(streak_len: int) -> int:
    """연속 일수 → 1~20 사이클 일차. 21일째는 다시 1일차."""
    return (streak_len - 1) % CYCLE_DAYS + 1


REWARDS = [reward_for_day(d) for d in range(1, CYCLE_DAYS + 1)]


def _to_kst_date(ts: datetime) -> date:
    if ts.tzinfo is None:  # sqlite 등 naive 는 UTC 로 본다.
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(KST).date()


async def _attendance_dates(session: AsyncSession, device: str) -> set[date]:
    rows = (
        await session.scalars(
            select(PointLedger.created_at).where(
                PointLedger.device_id == device,
                PointLedger.reason == ATTENDANCE_REASON,
            )
        )
    ).all()
    return {_to_kst_date(ts) for ts in rows}


def _run_ending(days: set[date], anchor: date) -> int:
    """anchor 부터 거꾸로 이어진 연속 일수."""
    n = 0
    d = anchor
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n


async def build_attendance(session: AsyncSession, device: str) -> dict[str, Any]:
    today = kst_today()
    days = await _attendance_dates(session, device)
    checked = today in days
    if checked:
        streak = _run_ending(days, today)
        cycle_day = _cycle_day(streak)  # 오늘 받은 일차
    else:
        prior = _run_ending(days, today - timedelta(days=1))
        streak = prior  # 어제까지의 연속(오늘 출석 전)
        cycle_day = _cycle_day(prior + 1)  # 출석 시 받게 될 일차
    return {
        "checkedToday": checked,
        "streak": streak,
        "cycleDay": cycle_day,
        "todayReward": reward_for_day(cycle_day),
        "cycleDays": CYCLE_DAYS,
        "rewards": REWARDS,
        "total": await total_points(session, device),
    }


async def check_in(session: AsyncSession, device: str) -> dict[str, Any]:
    today = kst_today()
    days = await _attendance_dates(session, device)
    if today in days:
        raise AlreadyCheckedIn
    streak = _run_ending(days, today - timedelta(days=1)) + 1
    cycle_day = _cycle_day(streak)
    reward = reward_for_day(cycle_day)
    session.add(
        PointLedger(device_id=device, amount=reward, reason=ATTENDANCE_REASON)
    )
    await session.commit()
    return {
        "cycleDay": cycle_day,
        "reward": reward,
        "streak": streak,
        "total": await total_points(session, device),
    }
