"""수확 인증 API 스키마."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel

from app.schemas.rewards import BadgeOut


class RecipeOut(BaseModel):
    name: str
    nutrients: dict[str, str] = {}


class CardLink(BaseModel):
    label: str
    url: str


class HarvestCard(BaseModel):
    cropSlug: str
    cropName: str
    category: str = ""
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


class HarvestRecordOut(BaseModel):
    id: int
    planId: int | None
    cropSlug: str
    cropName: str
    verified: bool
    confidence: float | None
    harvestedAt: date
    createdAt: datetime
