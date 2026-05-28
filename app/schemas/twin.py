from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.recommend import CropPhase

AlertType = Literal["pest", "price", "weather", "labor"]
Severity = Literal["low", "medium", "high"]


class MonthlyPoint(BaseModel):
    month: str
    monthNum: int = Field(ge=1, le=12)
    revenueManwon: int
    laborHours: int
    phase: CropPhase


class CrisisAlert(BaseModel):
    month: int = Field(ge=1, le=12)
    type: AlertType
    title: str
    detail: str
    severity: Severity


class TwinResponse(BaseModel):
    cropId: str
    name: str
    emoji: str
    totalRevenueManwon: int
    totalCostManwon: int
    totalLaborHours: int
    peakLaborMonth: int = Field(ge=1, le=12)
    monthly: list[MonthlyPoint]
    alerts: list[CrisisAlert]
    aiCoach: str
