from datetime import date

from pydantic import BaseModel, Field


class PricePoint(BaseModel):
    date: date
    price: int = Field(description="원/kg")
    is_forecast: bool = False
    forecast_low: int | None = None
    forecast_high: int | None = None


class ShippingDecision(BaseModel):
    score_today: int = Field(ge=1, le=5)
    score_in_3d: int = Field(ge=1, le=5)
    price_today: int
    price_in_3d: int
    recommendation: str
    delta_pct: float


class PeerFarmStat(BaseModel):
    region: str
    total_farms: int
    farms_aligned: int
    note: str


class ShippingDashboard(BaseModel):
    crop_id: str
    crop_name: str
    region: str
    updated_at: date
    decision: ShippingDecision
    price_series: list[PricePoint]
    peer: PeerFarmStat
