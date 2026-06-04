"""수확 인증 API 스키마."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


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
