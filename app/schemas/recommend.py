from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["returning", "weekend"]
AreaUnit = Literal["pyeong", "sqm", "hectare"]
FacilityType = Literal["open_field", "vinyl_house", "smart_farm"]
VisitFrequency = Literal["weekly_1", "weekly_2", "biweekly", "monthly"]
CropPhase = Literal["rest", "seeding", "growing", "harvest"]
CropColor = Literal["red", "orange", "indigo"]


class OnboardingInput(BaseModel):
    """Frontend 의 OnboardingInput 과 구조 일치."""

    mode: Mode
    region: str
    province: str | None = None
    area: float = Field(gt=0)
    areaUnit: AreaUnit
    laborCount: int = Field(ge=1)
    preferredCrops: list[str] = Field(default_factory=list)
    budgetManwon: int | None = Field(default=None, ge=0)
    facility: FacilityType | None = None
    visitFrequency: VisitFrequency | None = None


class CalendarMonth(BaseModel):
    month: int = Field(ge=1, le=12)
    phase: CropPhase


class CropRecommendationItem(BaseModel):
    cropId: str
    name: str
    emoji: str
    matchScore: int = Field(ge=0, le=100)
    difficulty: int = Field(ge=1, le=5)
    expectedRevenueManwon: int
    expectedNetManwon: int
    expectedYieldKg: int
    expectedDirectPriceWon: int
    llmReason: str
    tags: list[str]
    calendar: list[CalendarMonth]
    peerFarms: int
    peerAgreeRate: int = Field(ge=0, le=100)
    color: CropColor


class RecommendationResponse(BaseModel):
    mode: Mode
    items: list[CropRecommendationItem]
