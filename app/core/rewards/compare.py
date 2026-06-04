"""긍정형 비교 통계 — 커뮤니티 분포 대비 내 활동 위치.

기획서 Layer 4 '익명·긍정형 비교' 구현. 절대 순위표 없이 백분위만 보여주고,
중앙값 아래면 순위 대신 격려 문구로 전환한다 (하위권 좌절 → 이탈 방지).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rewards.streak import _active_days
from app.data import community
from app.db.models.harvest import HarvestRecord


def _top_percent(value: float, q: dict[str, int]) -> int:
    """주간 기록 횟수 → '상위 X%' (분위수 선형 보간, 5 단위 반올림)."""
    pts = [(0.0, 100.0), (q["p25"], 75.0), (q["p50"], 50.0), (q["p75"], 25.0), (q["p90"], 10.0)]
    if value >= q["p90"]:
        top = max(3.0, 10.0 - (value - q["p90"]) * 2)
    else:
        top = 100.0
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= value <= x1:
                top = y0 if x1 == x0 else y0 + (value - x0) * (y1 - y0) / (x1 - x0)
                break
    return max(3, min(100, round(top / 5) * 5))


def _weekly_message(active_days: int, top: int, q: dict[str, int]) -> tuple[bool, str]:
    """(상위권 여부, 긍정형 문구). 중앙값 미만이면 격려 문구."""
    if top <= 50:
        return True, f"이번 주 {active_days}회 기록 — 함께 키우는 사람들 중 상위 {top}%예요"
    need = max(1, q["p50"] - active_days)
    return False, (
        f"이번 주 {active_days}회 기록했어요. {need}회만 더 기록하면 절반 이상을 앞질러요!"
    )


async def build_compare(
    session: AsyncSession, crop_slug: str | None = None
) -> dict[str, Any]:
    today = date.today()
    week_ago = today - timedelta(days=6)
    days = await _active_days(session)
    weekly = sum(1 for d in days if week_ago <= d <= today)

    all_stats = community.overall()
    top = _top_percent(weekly, all_stats["weeklyRecords"])
    positive, message = _weekly_message(weekly, top, all_stats["weeklyRecords"])

    out: dict[str, Any] = {
        "weeklyActiveDays": weekly,
        "topPercent": top,
        "aboveMedian": positive,
        "message": message,
        "communitySize": all_stats["growers"],
    }

    if crop_slug:
        stats = community.for_crop(crop_slug)
        if stats:
            harvested = (
                await session.execute(
                    select(HarvestRecord.id)
                    .where(
                        HarvestRecord.crop_slug == crop_slug,
                        HarvestRecord.verified.is_(True),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none() is not None
            rate = round(stats["completionRate"] * 100)
            if harvested:
                crop_msg = (
                    f"{stats['cropName']}을(를) 키우는 {stats['growers']}명 중 "
                    f"{rate}%만 수확까지 성공했어요 — 당신도 그중 하나!"
                )
            else:
                crop_msg = (
                    f"지금 {stats['growers']}명이 {stats['cropName']}을(를) 함께 키우고 있어요. "
                    f"수확까지 가는 사람은 {rate}% — 완주에 도전해보세요!"
                )
            out["crop"] = {
                "cropSlug": crop_slug,
                "cropName": stats["cropName"],
                "growers": stats["growers"],
                "completionRate": stats["completionRate"],
                "harvested": harvested,
                "message": crop_msg,
            }

    return out
