"""뱃지 — 로컬 카탈로그(app/data/badges.json) 기반 규칙 업적.

뱃지는 **달성(met) ≠ 획득(claimed)**. 조건을 채우면 met=True 가 되고, 사용자가 뱃지 도감에서
직접 '획득'(claim)해야 PointLedger(reason='badge:<id>')에 팜이 적립된다(claimed=True, 스티키).
작물 수확 레벨 팜(reason='clv:...')은 도감 표시용이라 조회 시 자동 적립(sync_crop_rewards).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rewards.attendance import build_attendance
from app.core.rewards.collection import build_collection
from app.core.rewards.crop_level import crop_level, level_reward
from app.core.rewards.points import build_points, total_points
from app.core.rewards.streak import build_streak
from app.db.models.point import PointLedger

_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "badges.json"


def _load_catalog() -> list[dict[str, Any]]:
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


# 카탈로그는 정적이라 임포트 시 1회 로드. 항목: id·emoji·name·description·difficulty·rewardFarm·metric·threshold
BADGE_CATALOG: list[dict[str, Any]] = _load_catalog()
_BADGE_REASON_PREFIX = "badge:"

# 작물 수확 레벨업 팜 지급(레벨 표시는 도감). reason: clv:{slug}:{lv}. 레벨 계산=crop_level 모듈.
_CROP_REASON_PREFIX = "clv:"


class BadgeNotFound(Exception):
    """존재하지 않는 뱃지 id."""


class BadgeNotMet(Exception):
    """아직 달성 조건 미충족."""


class BadgeAlreadyClaimed(Exception):
    """이미 획득한 뱃지."""


async def _stats(session: AsyncSession, device_id: str) -> dict[str, int]:
    col = await build_collection(session, device_id)
    stk = await build_streak(session, device_id)
    pts = await build_points(session, device_id)
    att = await build_attendance(session, device_id)
    return {
        "totalHarvests": col["totalHarvests"],
        "collectedCrops": col["collectedCrops"],
        "bestStreak": stk["best"],
        "totalActiveDays": stk["totalActiveDays"],
        "memoCount": pts["memoCount"],
        "photoCount": pts["photoCount"],
        "attendanceStreak": att["streak"],  # 현재 연속(비단조)
        "attendanceBest": att["best"],  # 역대 최고 연속(단조 — 뱃지 영구 달성)
    }


async def _claimed_ids(session: AsyncSession, device_id: str) -> set[str]:
    """이미 팜을 획득한 뱃지 id 집합."""
    rows = (
        await session.scalars(
            select(PointLedger.reason).where(
                PointLedger.device_id == device_id,
                PointLedger.reason.like(f"{_BADGE_REASON_PREFIX}%"),
            )
        )
    ).all()
    return {r[len(_BADGE_REASON_PREFIX) :] for r in rows}


def _evaluate(stats: dict[str, int], claimed: set[str]) -> list[dict[str, Any]]:
    out = []
    for b in BADGE_CATALOG:
        value = stats.get(b["metric"], 0)
        threshold = b["threshold"]
        is_claimed = b["id"] in claimed
        met = value >= threshold
        # 달성은 스티키 — 현재 충족 OR 이미 획득.
        achieved = met or is_claimed
        out.append(
            {
                "id": b["id"],
                "emoji": b["emoji"],
                "name": b["name"],
                "description": b["description"],
                "difficulty": b["difficulty"],
                "rewardFarm": b["rewardFarm"],
                "achieved": achieved,  # 조건 충족(스티키)
                "claimed": is_claimed,  # 팜 획득 완료
                "claimable": achieved and not is_claimed,  # 지금 획득 가능
                "progress": 1.0 if achieved else min(1.0, value / threshold),
                "current": value,
                "threshold": threshold,
            }
        )
    return out


async def build_badges(session: AsyncSession, device_id: str) -> list[dict[str, Any]]:
    stats = await _stats(session, device_id)
    claimed = await _claimed_ids(session, device_id)
    return _evaluate(stats, claimed)


async def claim_badge(
    session: AsyncSession, device_id: str, badge_id: str
) -> dict[str, Any]:
    """뱃지 획득 — 달성했고 미획득이면 팜 적립. BadgeNotFound/NotMet/AlreadyClaimed 예외."""
    badge = next((b for b in BADGE_CATALOG if b["id"] == badge_id), None)
    if badge is None:
        raise BadgeNotFound
    claimed = await _claimed_ids(session, device_id)
    if badge_id in claimed:
        raise BadgeAlreadyClaimed
    stats = await _stats(session, device_id)
    if stats.get(badge["metric"], 0) < badge["threshold"]:
        raise BadgeNotMet
    session.add(
        PointLedger(
            device_id=device_id,
            amount=badge["rewardFarm"],
            reason=f"{_BADGE_REASON_PREFIX}{badge_id}",
        )
    )
    await session.commit()
    return {
        "id": badge_id,
        "name": badge["name"],
        "rewardFarm": badge["rewardFarm"],
        "total": await total_points(session, device_id),
    }


async def _awarded_crop_levels(
    session: AsyncSession, device_id: str
) -> set[tuple[str, int]]:
    """이미 지급한 (작물slug, 레벨) 집합."""
    rows = (
        await session.scalars(
            select(PointLedger.reason).where(
                PointLedger.device_id == device_id,
                PointLedger.reason.like(f"{_CROP_REASON_PREFIX}%"),
            )
        )
    ).all()
    res: set[tuple[str, int]] = set()
    for r in rows:
        slug, _, lv = r[len(_CROP_REASON_PREFIX) :].rpartition(":")
        if slug and lv.isdigit():
            res.add((slug, int(lv)))
    return res


async def sync_crop_rewards(
    session: AsyncSession, device_id: str
) -> list[dict[str, Any]]:
    """작물 수확 레벨업 팜을 자동 적립(멱등). 도감/요약/수확 조회 시 호출. 커밋 포함."""
    col = await build_collection(session, device_id)
    awarded_crop = await _awarded_crop_levels(session, device_id)
    newly: list[dict[str, Any]] = []
    for e in col["entries"]:
        if not e["collected"]:
            continue
        level = crop_level(e["harvestCount"])
        for lv in range(1, level + 1):
            if (e["cropSlug"], lv) in awarded_crop:
                continue
            session.add(
                PointLedger(
                    device_id=device_id,
                    amount=level_reward(lv),
                    reason=f"{_CROP_REASON_PREFIX}{e['cropSlug']}:{lv}",
                )
            )
            newly.append(
                {
                    "cropSlug": e["cropSlug"],
                    "level": lv,
                    "rewardFarm": level_reward(lv),
                }
            )
    if newly:
        await session.commit()
    return newly


async def achieved_ids(session: AsyncSession, device_id: str) -> set[str]:
    """달성(met)한 뱃지 id 집합 — 수확 인증 전후 diff(새 뱃지 연출)용."""
    return {b["id"] for b in await build_badges(session, device_id) if b["achieved"]}
