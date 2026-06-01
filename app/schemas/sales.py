"""판매 도우미 스키마 — 직매장 위치 추천 + 채널별 수익 비교."""

from pydantic import BaseModel, Field


class StoreOut(BaseModel):
    id: int
    name: str
    operator: str = ""
    sido: str = ""
    sigungu: str = ""
    address: str = ""
    phone: str = ""
    opened: str = ""
    lat: float | None = None
    lng: float | None = None
    distance_km: float | None = None


class NearbyResponse(BaseModel):
    found: bool
    origin_lat: float | None = None
    origin_lng: float | None = None
    origin_label: str | None = None
    stores: list[StoreOut] = Field(default_factory=list)
    message: str | None = None


class ChannelOut(BaseModel):
    key: str                       # "direct" | "wholesale"
    label: str
    source_price: int              # KAMIS 원단가 (원/unit)
    source_unit: str
    unit_price: float | None       # 기준단위(kg 또는 개)당 판매단가 (보정 후)
    gross: int | None              # 총 판매액
    commission_pct: float
    net: int | None                # 실수령액
    note: str
    estimated: bool


class CompareResponse(BaseModel):
    found: bool
    crop_name: str
    amount: float | None = None    # 입력 판매량
    amount_unit: str = "kg"        # 'kg' | '개'
    input_mode: str = "weight"     # 'weight' | 'count'
    obs_date: str | None = None
    channels: list[ChannelOut] = Field(default_factory=list)
    best_key: str | None = None
    delta_net: int | None = None
    message: str | None = None


# ───────────── 위치 기반 채널 추천 (가격 + 운송비 + AI) ─────────────


class MarketOut(BaseModel):
    id: int
    name: str
    category: str = ""
    sido: str = ""
    opened: str = ""
    corp_count: int | None = None
    merchant_count: int | None = None
    lat: float | None = None
    lng: float | None = None
    distance_km: float | None = None


class PlaceOut(BaseModel):
    kind: str                      # 'direct' | 'wholesale'
    name: str
    distance_km: float
    sido: str = ""
    address: str = ""
    phone: str = ""
    lat: float | None = None
    lng: float | None = None


class RecommendChannelOut(BaseModel):
    key: str
    label: str
    net: int | None                # 가격 실수령 (운송 전)
    unit_price: float | None
    source_price: int
    source_unit: str
    commission_pct: float
    note: str
    estimated: bool
    place: PlaceOut | None = None
    transport_cost: int | None = None
    net_after: int | None = None   # 운송비 차감 최종


class RecommendResponse(BaseModel):
    found: bool
    crop_name: str
    amount: float | None = None
    amount_unit: str = "kg"
    input_mode: str = "weight"
    obs_date: str | None = None
    origin_lat: float | None = None
    origin_lng: float | None = None
    origin_label: str | None = None
    channels: list[RecommendChannelOut] = Field(default_factory=list)
    best_key: str | None = None
    delta_net_after: int | None = None
    per_km_won: int = 0
    advice: str = ""
    advice_source: str = "none"    # 'ai' | 'rule' | 'none'
    nearby_direct: list[StoreOut] = Field(default_factory=list)
    nearby_wholesale: list[MarketOut] = Field(default_factory=list)
    message: str | None = None
