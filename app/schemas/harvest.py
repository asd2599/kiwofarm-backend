"""수확 인증 API 스키마."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.farmplan import TaskMemoOut
from app.schemas.rewards import BadgeOut


class RecipeOut(BaseModel):
    name: str
    materials: str = ""  # 재료 (농사로 「이달의음식」 또는 AI 생성)
    cooking: str = ""  # 조리 단계
    nutrients: dict[str, str] = {}


class CardLink(BaseModel):
    label: str
    url: str


class HarvestCard(BaseModel):
    cropSlug: str
    cropName: str
    category: str = ""
    difficulty: int | None = None
    daysToHarvest: list[int] | None = None  # [최소, 최대] 일
    source: str  # "nongsaro:monthFd" | "ai"
    storage: str = ""
    eating: str = ""
    nutrition: str = ""
    seasonMonths: list[int] = []
    recipes: list[RecipeOut] = []
    links: list[CardLink] = []


class VerdictOut(BaseModel):
    crop_match: bool
    is_harvest: bool
    freshness: int
    quantity: str
    fake_suspect: bool
    confidence: float
    reason: str


class HarvestVerifyResponse(BaseModel):
    verified: bool
    demoMode: bool = False
    recordId: int | None = None
    verdict: VerdictOut | None = None
    warnings: list[str] = []
    card: HarvestCard | None = None  # 통과 시에만
    newBadges: list[BadgeOut] = []  # 이번 인증으로 새로 달성한 뱃지
    message: str = ""


class JournalVerifyIn(BaseModel):
    planId: int


class JournalVerdictOut(BaseModel):
    crop_match: bool
    growth_consistent: bool
    care_consistent: bool
    has_harvest: bool
    fake_suspect: bool
    quantity: str
    confidence: float
    reason: str
    summary: str


class HarvestJournalResponse(BaseModel):
    verified: bool
    demoMode: bool = False
    recordId: int | None = None
    verdict: JournalVerdictOut | None = None
    warnings: list[str] = []
    card: HarvestCard | None = None  # 통과 시에만
    newBadges: list[BadgeOut] = []  # 이번 인증으로 새로 달성한 뱃지
    pointsTotal: int = 0  # 인증 반영 후 누적 점수
    message: str = ""


class HarvestRecordOut(BaseModel):
    id: int
    planId: int | None
    cropSlug: str
    cropName: str
    verified: bool
    confidence: float | None
    harvestedAt: date
    createdAt: datetime


class CropJournalOut(BaseModel):
    """도감 카드 '내 기록' 탭 — 해당 작물을 키우며 남긴 메모·사진."""

    cropSlug: str
    cropName: str
    totalMemos: int
    totalPhotos: int
    memos: list[TaskMemoOut] = []  # memo_date 내림차순
