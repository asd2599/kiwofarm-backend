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
    level: int  # 수확 레벨(0~maxLevel)
    maxLevel: int
    nextLevelAt: int | None = None  # 다음 레벨 도달 수확 횟수(최고 레벨이면 None)
    nextReward: int | None = None  # 다음 레벨 보상 팜
    levelProgress: float = 0.0  # 다음 레벨까지 진행도 0~1
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
    difficulty: int
    rewardFarm: int
    achieved: bool  # 조건 충족(스티키)
    claimed: bool  # 팜 획득 완료
    claimable: bool  # 지금 획득 가능(달성+미획득)
    progress: float
    current: int
    threshold: int


class BadgeClaimOut(BaseModel):
    id: str
    name: str
    rewardFarm: int
    total: int  # 획득 후 보유 팜


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


class AttendanceMilestone(BaseModel):
    days: int  # 연속 일수 목표(7·14·30)
    reward: int  # 달성 보너스 팜
    reached: bool  # 역대 최고 연속으로 달성했는지(스티키)


class AttendanceOut(BaseModel):
    checkedToday: bool
    dailyReward: int  # 매일 출석 기본 팜
    streak: int  # 현재 연속 출석 일수
    best: int  # 역대 최고 연속
    monthDays: int  # 이번 달(달력 월) 출석 일수
    monthTarget: int  # 월간 보너스 목표 일수(20)
    monthBonus: int  # 월간 달성 보너스 팜
    monthAchieved: bool  # 이번 달 목표 달성(또는 보너스 지급 완료)
    monthBest: int  # 역대 한 달 최다 출석 일수
    milestones: list[AttendanceMilestone]  # 연속 마일스톤 현황
    total: int  # 현재 보유 팜


class AttendanceBonus(BaseModel):
    type: str  # 'month' | 'streak'
    label: str
    reward: int


class AttendanceClaimOut(BaseModel):
    reward: int  # 일일 기본 팜
    bonusReward: int  # 이번 출석으로 추가로 받은 보너스 합
    bonuses: list[AttendanceBonus]  # 새로 달성한 보너스 목록(연출용)
    streak: int
    monthDays: int
    total: int


class RewardsSummary(BaseModel):
    collection: CollectionOut
    badges: list[BadgeOut]
    streak: StreakOut
    points: PointsOut
    attendance: AttendanceOut
    compare: CompareOut | None = None
