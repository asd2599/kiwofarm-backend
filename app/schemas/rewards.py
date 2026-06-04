"""보상(도감·뱃지·Streak) API 스키마."""

from __future__ import annotations

from pydantic import BaseModel


class CollectionEntry(BaseModel):
    cropSlug: str
    cropName: str
    category: str = ""
    difficulty: int | None = None
    collected: bool
    harvestCount: int
    firstHarvestedAt: str | None = None
    lastHarvestedAt: str | None = None


class CollectionOut(BaseModel):
    totalCrops: int
    collectedCrops: int
    totalHarvests: int
    entries: list[CollectionEntry]


class BadgeOut(BaseModel):
    id: str
    emoji: str
    name: str
    description: str
    achieved: bool
    progress: float
    current: int
    threshold: int


class StreakOut(BaseModel):
    current: int
    best: int
    todayLogged: bool
    totalActiveDays: int


class CropCompareOut(BaseModel):
    cropSlug: str
    cropName: str
    growers: int
    completionRate: float
    harvested: bool
    message: str


class CompareOut(BaseModel):
    weeklyActiveDays: int
    topPercent: int  # 상위 X%
    aboveMedian: bool
    message: str  # 긍정형 문구 (중앙값 미만이면 격려)
    communitySize: int
    crop: CropCompareOut | None = None


class RewardsSummary(BaseModel):
    collection: CollectionOut
    badges: list[BadgeOut]
    streak: StreakOut
    compare: CompareOut | None = None
