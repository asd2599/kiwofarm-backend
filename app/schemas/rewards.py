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


class PointsOut(BaseModel):
    total: int
    memoCount: int
    photoCount: int
    harvestCount: int


class AttendanceOut(BaseModel):
    checkedToday: bool
    streak: int  # 현재 연속 출석 일수
    cycleDay: int  # 오늘 해당 1~20 사이클 일차(미출석이면 출석 시 받게 될 일차)
    todayReward: int  # 오늘 출석으로 받는/받은 팜
    cycleDays: int  # 사이클 길이(20)
    rewards: list[int]  # 길이 20 일차별 보상표
    total: int  # 현재 보유 팜


class AttendanceClaimOut(BaseModel):
    cycleDay: int
    reward: int
    streak: int
    total: int


class RewardsSummary(BaseModel):
    collection: CollectionOut
    badges: list[BadgeOut]
    streak: StreakOut
    points: PointsOut
    attendance: AttendanceOut
    compare: CompareOut | None = None
