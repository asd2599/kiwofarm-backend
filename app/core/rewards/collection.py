"""작물 도감 — 40종 마스터 × 수확 인증 기록 집계."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.planting import matrix
from app.db.models.harvest import HarvestRecord


async def build_collection(session: AsyncSession, device_id: str) -> dict[str, Any]:
    """도감: 마스터 40종 전체를 돌려주되 수확한 작물에 기록을 채운다."""
    rows = (
        await session.execute(
            select(HarvestRecord)
            .where(
                HarvestRecord.verified.is_(True),
                HarvestRecord.device_id == device_id,
            )
            .order_by(HarvestRecord.harvested_at)
        )
    ).scalars().all()

    by_slug: dict[str, list[HarvestRecord]] = {}
    for r in rows:
        by_slug.setdefault(r.crop_slug, []).append(r)

    entries = []
    for crop in matrix.all_crops():
        recs = by_slug.get(crop["id"], [])
        entries.append(
            {
                "cropSlug": crop["id"],
                "cropName": crop["name"],
                "category": crop.get("category", ""),
                "difficulty": crop.get("difficulty"),
                "collected": bool(recs),
                "harvestCount": len(recs),
                "firstHarvestedAt": recs[0].harvested_at.isoformat() if recs else None,
                "lastHarvestedAt": recs[-1].harvested_at.isoformat() if recs else None,
            }
        )

    collected = sum(1 for e in entries if e["collected"])
    return {
        "totalCrops": len(entries),
        "collectedCrops": collected,
        "totalHarvests": len(rows),
        "entries": entries,
    }
