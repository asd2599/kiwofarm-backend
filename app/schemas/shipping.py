from datetime import date

from pydantic import BaseModel, Field


class PricePoint(BaseModel):
    date: date
    price: int = Field(description="원/단위 (KAMIS 도매가)")
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


class RegionOption(BaseModel):
    """KAMIS 응답에서 추출한 가용 지역(=도시) 한 곳."""

    county: str  # "서울", "부산" 등 (특수값 "평균"/"평년" 제외)
    market: str  # 대표 시장명 ("가락도매" 등)
    latest_price: int
    sample_count: int


class ShippingDashboard(BaseModel):
    """지역 선택 완료 후 대시보드."""

    crop_id: str
    crop_name: str
    region: str
    market: str
    updated_at: date
    decision: ShippingDecision
    price_series: list[PricePoint]
    peer: PeerFarmStat


class ShippingRegions(BaseModel):
    """지역 미선택 시 가용 지역 목록만."""

    crop_id: str
    crop_name: str
    regions: list[RegionOption]
    note: str | None = None  # 데이터 없음 등 안내


class ForecastResponse(BaseModel):
    """도매가 예측 (Prophet, 폴백=선형추세)."""

    found: bool
    crop_name: str
    unit: str | None = None
    method: str | None = None  # "prophet" / "linear"
    horizon_days: int = 0
    series: list[PricePoint] = []  # 최근 실측 + 예측 (is_forecast 로 구분)
    forecast_last: int | None = None        # 마지막 예측가
    forecast_last_low: int | None = None
    forecast_last_high: int | None = None
    message: str | None = None


class TrendPoint(BaseModel):
    label: str
    price: int


class PriceTrendResponse(BaseModel):
    """최근 가격추이 (KAMIS recentlyPriceTrendList)."""

    found: bool
    crop_name: str
    unit: str | None = None
    rank: str | None = None
    points: list[TrendPoint] = []  # 올해 추이 (과거→최근)
    latest: int | None = None
    year_ago: int | None = None   # 작년 동기
    normal: int | None = None     # 평년 동기
    month_high: int | None = None
    month_low: int | None = None
    message: str | None = None


class ShippingAdviceResponse(BaseModel):
    """AI 출하 조언 (KAMIS 3종 피처 → GPT-4o)."""

    found: bool
    crop_name: str
    advice: str
    source: str  # "ai" / "rule" / "none"
    current_price: int | None = None
    unit: str | None = None
    vs_prev_pct: float | None = None
    vs_year_ago_pct: float | None = None
    vs_normal_pct: float | None = None
    trend_pct: float | None = None
    volatility_pct: float | None = None
    direction: str | None = None
    forecast_price: int | None = None
    forecast_pct: float | None = None
    forecast_days: int | None = None
    message: str | None = None


class RecentPriceResponse(BaseModel):
    """검색한 작물의 최근일자 도매가 (KAMIS dailyPriceByCategoryList)."""

    found: bool
    crop_name: str
    item_code: str
    product_cls: str | None = None  # "도매" / "소매"
    kind_name: str | None = None
    rank: str | None = None
    unit: str | None = None
    obs_date: date | None = None
    price: int | None = None
    prev_price: int | None = None
    delta_pct: float | None = None  # 전일 대비 등락률
    message: str | None = None
