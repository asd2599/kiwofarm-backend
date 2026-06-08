"""뱃지 — 도감·Streak·누적 인증에서 파생되는 규칙 기반 업적.

획득 시점 테이블 없이 매번 계산한다. '새 뱃지' 연출은 수확 인증 직전/직후
상태를 비교(diff)해서 만든다 (api/v1/harvest 참조).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rewards.collection import build_collection
from app.core.rewards.streak import build_streak

# (id, 이모지, 이름, 설명, 평가함수(stats) -> (달성여부, 진행률 0~1))
BADGE_DEFS: tuple[tuple[str, str, str, str, str, int], ...] = (
    # id, emoji, name, desc, metric, threshold
    ("first_harvest", "🌱", "첫 수확", "첫 수확 인증에 성공했어요", "totalHarvests", 1),
    ("harvester_10", "📷", "부지런한 농부", "수확 인증 10회 달성", "totalHarvests", 10),
    ("collector_3", "🥬", "새싹 콜렉터", "작물 도감 3종 수집", "collectedCrops", 3),
    ("collector_5", "🧺", "텃밭 콜렉터", "작물 도감 5종 수집", "collectedCrops", 5),
    ("collector_10", "🏆", "도감 마스터", "작물 도감 10종 수집", "collectedCrops", 10),
    ("streak_7", "🔥", "일주일 개근", "7일 연속 기록", "bestStreak", 7),
    ("streak_30", "💎", "한 달 개근", "30일 연속 기록", "bestStreak", 30),
)


async def _stats(session: AsyncSession, device_id: str) -> dict[str, int]:
    col = await build_collection(session, device_id)
    stk = await build_streak(session, device_id)
    return {
        "totalHarvests": col["totalHarvests"],
        "collectedCrops": col["collectedCrops"],
        "bestStreak": stk["best"],
    }


def _evaluate(stats: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for bid, emoji, name, desc, metric, threshold in BADGE_DEFS:
        value = stats.get(metric, 0)
        out.append(
            {
                "id": bid,
                "emoji": emoji,
                "name": name,
                "description": desc,
                "achieved": value >= threshold,
                "progress": min(1.0, value / threshold),
                "current": value,
                "threshold": threshold,
            }
        )
    return out


async def build_badges(session: AsyncSession, device_id: str) -> list[dict[str, Any]]:
    return _evaluate(await _stats(session, device_id))


async def achieved_ids(session: AsyncSession, device_id: str) -> set[str]:
    """달성한 뱃지 id 집합 — 수확 인증 전후 diff 용."""
    return {b["id"] for b in await build_badges(session, device_id) if b["achieved"]}
