"""출석 보상 — PointLedger 파생. 별도 테이블 없음.

세 갈래 보상(연속이 아니라 '월 누적 20일'이 주 보상):
  - 매일 출석: 'attendance' 원장 row(KST 날짜 1개) + 기본 10팜. 하루 1회.
  - 월간 보너스: 달력 월(KST) 출석 20일 달성 시 1회 100팜
    ('attendance:month:YYYY-MM', 월마다 멱등).
  - 연속 보너스: 7·14·30일 연속 출석 달성 시 1회씩 보너스
    ('attendance:streak:{n}', 생애 1회 멱등).
출석일 = 'attendance' 원장 row 의 KST 날짜 집합으로 판정.
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
_MONTH_PREFIX = "attendance:month:"
_STREAK_PREFIX = "attendance:streak:"

DAILY_REWARD = 10
MONTHLY_TARGET = 20  # 달력 월 출석 일수 목표
MONTHLY_BONUS = 100
# 연속 출석 마일스톤(연속 일수 → 1회성 보너스 팜). 생애 1회 멱등 지급.
STREAK_MILESTONES: dict[int, int] = {7: 20, 14: 50, 30: 120}


class AlreadyCheckedIn(Exception):
    """오늘 이미 출석함."""


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


def _best_run(days: set[date]) -> int:
    """역대 최장 연속 출석 일수(단조증가 — 뱃지 영구 달성용)."""
    if not days:
        return 0
    ordered = sorted(days)
    best = run = 1
    for prev, cur in zip(ordered, ordered[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        best = max(best, run)
    return best


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _month_count(days: set[date], ref: date) -> int:
    """ref 가 속한 달력 월의 출석 일수."""
    return sum(1 for d in days if d.year == ref.year and d.month == ref.month)


def _month_best(days: set[date]) -> int:
    """역대 한 달 최다 출석 일수(단조 — 월간 개근 뱃지 영구 달성용)."""
    counts: dict[tuple[int, int], int] = {}
    for d in days:
        counts[(d.year, d.month)] = counts.get((d.year, d.month), 0) + 1
    return max(counts.values(), default=0)


async def _granted_suffixes(
    session: AsyncSession, device: str, prefix: str
) -> set[str]:
    """이미 지급한 보너스 reason 의 접미사 집합(멱등 판정용)."""
    rows = (
        await session.scalars(
            select(PointLedger.reason).where(
                PointLedger.device_id == device,
                PointLedger.reason.like(f"{prefix}%"),
            )
        )
    ).all()
    return {r[len(prefix) :] for r in rows}


def _milestones_state(best: int) -> list[dict[str, Any]]:
    """연속 마일스톤 표시용(달성=역대 최고 연속 기준, 스티키)."""
    return [
        {"days": d, "reward": STREAK_MILESTONES[d], "reached": best >= d}
        for d in sorted(STREAK_MILESTONES)
    ]


async def build_attendance(session: AsyncSession, device: str) -> dict[str, Any]:
    today = kst_today()
    days = await _attendance_dates(session, device)
    checked = today in days
    anchor = today if checked else today - timedelta(days=1)
    streak = _run_ending(days, anchor)
    month_days = _month_count(days, today)
    month_granted = _month_key(today) in await _granted_suffixes(
        session, device, _MONTH_PREFIX
    )
    # 이번 달 출석한 '일(day)' 번호 — 달력 렌더용.
    month_attended = sorted(
        d.day for d in days if d.year == today.year and d.month == today.month
    )
    return {
        "checkedToday": checked,
        "dailyReward": DAILY_REWARD,
        "streak": streak,  # 현재 연속(비단조)
        "best": _best_run(days),  # 역대 최고 연속(단조 — 뱃지/마일스톤 판정용)
        "monthDays": month_days,  # 이번 달 출석 일수
        "monthTarget": MONTHLY_TARGET,
        "monthBonus": MONTHLY_BONUS,
        "monthAchieved": month_granted or month_days >= MONTHLY_TARGET,
        "monthBest": _month_best(days),  # 역대 한 달 최다 출석(월간 개근 뱃지용)
        "monthAttendedDays": month_attended,  # 이번 달 출석한 날짜(일) 목록
        "todayDay": today.day,  # KST 오늘 날짜(일) — 달력 강조용
        "milestones": _milestones_state(_best_run(days)),
        "total": await total_points(session, device),
    }


async def check_in(session: AsyncSession, device: str) -> dict[str, Any]:
    today = kst_today()
    days = await _attendance_dates(session, device)
    if today in days:
        raise AlreadyCheckedIn

    # 1) 일일 출석 기록(기본 팜)
    session.add(
        PointLedger(device_id=device, amount=DAILY_REWARD, reason=ATTENDANCE_REASON)
    )
    days = days | {today}
    streak = _run_ending(days, today)
    bonuses: list[dict[str, Any]] = []

    # 2) 월간 20일 달성 보너스(달력 월 1회)
    month_days = _month_count(days, today)
    mkey = _month_key(today)
    if (
        month_days >= MONTHLY_TARGET
        and mkey not in await _granted_suffixes(session, device, _MONTH_PREFIX)
    ):
        session.add(
            PointLedger(
                device_id=device, amount=MONTHLY_BONUS, reason=f"{_MONTH_PREFIX}{mkey}"
            )
        )
        bonuses.append(
            {
                "type": "month",
                "label": f"이번 달 {MONTHLY_TARGET}일 출석",
                "reward": MONTHLY_BONUS,
            }
        )

    # 3) 연속 마일스톤 보너스(생애 1회 멱등)
    streak_granted = await _granted_suffixes(session, device, _STREAK_PREFIX)
    for days_req, reward in sorted(STREAK_MILESTONES.items()):
        if streak >= days_req and str(days_req) not in streak_granted:
            session.add(
                PointLedger(
                    device_id=device,
                    amount=reward,
                    reason=f"{_STREAK_PREFIX}{days_req}",
                )
            )
            bonuses.append(
                {"type": "streak", "label": f"{days_req}일 연속 출석", "reward": reward}
            )

    await session.commit()
    return {
        "reward": DAILY_REWARD,
        "bonusReward": sum(b["reward"] for b in bonuses),
        "bonuses": bonuses,
        "streak": streak,
        "monthDays": month_days,
        "total": await total_points(session, device),
    }
