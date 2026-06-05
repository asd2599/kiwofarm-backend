"""연속 기록 Streak — 일지 메모(task_memo) + 수확 인증일 합산 (듀오링고 패턴).

'기록한 날' = 메모를 쓴 날 ∪ 수확 인증한 날. current 는 오늘(아직 오늘 기록이
없으면 어제)부터 거꾸로 이어진 일수, best 는 역대 최장 연속 일수.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.farm_plan import FarmPlan, TaskMemo
from app.db.models.harvest import HarvestRecord


async def _active_days(session: AsyncSession, device_id: str) -> set[date]:
    memo_days = (
        await session.execute(
            select(TaskMemo.memo_date)
            .join(FarmPlan, TaskMemo.plan_id == FarmPlan.id)
            .where(FarmPlan.device_id == device_id)
        )
    ).scalars().all()
    harvest_days = (
        await session.execute(
            select(HarvestRecord.harvested_at).where(
                HarvestRecord.device_id == device_id
            )
        )
    ).scalars().all()
    return set(memo_days) | set(harvest_days)


def _streaks(days: set[date], today: date) -> tuple[int, int]:
    if not days:
        return 0, 0
    # current: 오늘(기록 없으면 어제)을 기점으로 거꾸로 연속
    anchor = today if today in days else today - timedelta(days=1)
    current = 0
    d = anchor
    while d in days:
        current += 1
        d -= timedelta(days=1)
    # best: 정렬 후 최장 연속 구간
    ordered = sorted(days)
    best = run = 1
    for prev, cur in zip(ordered, ordered[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)
    return current, best


async def build_streak(session: AsyncSession, device_id: str) -> dict[str, Any]:
    days = await _active_days(session, device_id)
    today = date.today()
    current, best = _streaks(days, today)
    return {
        "current": current,
        "best": best,
        "todayLogged": today in days,
        "totalActiveDays": len(days),
    }
