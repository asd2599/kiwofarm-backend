"""출하 조언용 피처 추출 (결정론적).

KAMIS 3종 데이터를 모아 가격 지표를 계산한다. LLM 은 이 피처만 받아 해석한다
(직접 가격 예측 금지). 순수 계산이라 단위 테스트 가능.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from app.core.shipping.forecast import forecast_prices
from app.data import kamis

_FORECAST_DAYS = 7


@dataclass
class ShippingFeatures:
    crop_name: str
    unit: str
    current_price: int | None       # ① 최근 도매가
    prev_price: int | None          # ① 전일
    vs_prev_pct: float | None        # 전일 대비
    vs_year_ago_pct: float | None    # ② 작년 동기 대비
    vs_normal_pct: float | None      # ② 평년 대비
    trend_pct: float | None          # ② 최근 한 달 추세(첫→마지막)
    volatility_pct: float | None     # ③ 최근 일별 변동성(표준편차/평균)
    period_high: int | None          # ② 기간 최고
    period_low: int | None           # ② 기간 최저
    direction: str                   # 상승 / 하락 / 보합
    samples: int                     # ③ 일별 표본 수
    forecast_price: int | None       # 예측: N일 후 도매가
    forecast_pct: float | None       # 예측: 최근 실측 대비 변화율
    forecast_days: int               # 예측 기간(일)

    def as_metrics(self) -> dict:
        return asdict(self)


def _pct(cur: int | None, base: int | None) -> float | None:
    if cur is None or not base:
        return None
    return round((cur - base) / base * 100, 1)


def _direction(*pcts: float | None) -> str:
    val = next((p for p in pcts if p is not None), None)
    if val is None:
        return "보합"
    if val >= 2:
        return "상승"
    if val <= -2:
        return "하락"
    return "보합"


def _display_name(item_name: str, kind_name: str) -> str:
    if not kind_name or kind_name == item_name:
        return item_name or kind_name
    return f"{item_name} — {kind_name}"


async def build_shipping_features(
    category_code: str,
    item_code: str,
    kind_code: str = "",
    item_name: str = "",
    kind_name: str = "",
) -> ShippingFeatures:
    recent = await kamis.fetch_recent_price(category_code, item_code, kind_code, "02")
    trend = await kamis.fetch_price_trend(item_code, kind_code)

    today = date.today()
    points = await kamis.fetch_wholesale_period(
        category_code=category_code,
        item_code=item_code,
        kind_code=kind_code,
        start=today - timedelta(days=90),
        end=today,
    )
    by_county = kamis.group_by_county(points)
    avg_points = by_county.get("평균") or []
    avg_series = [p.price for p in avg_points[-30:]]  # 변동성은 최근 30거래일

    current = recent.price if recent else (trend.latest if trend else None)
    prev = recent.prev_price if recent else None
    unit = (recent.unit if recent else (trend.unit if trend else "")) or ""

    vs_prev = _pct(current, prev)
    vs_year = _pct(current, trend.year_ago) if trend else None
    vs_normal = _pct(current, trend.normal) if trend else None

    trend_pct = None
    if trend and len(trend.points) >= 2:
        trend_pct = _pct(trend.points[-1]["price"], trend.points[0]["price"])

    volatility = None
    if len(avg_series) >= 3:
        mean = statistics.mean(avg_series)
        if mean:
            volatility = round(statistics.pstdev(avg_series) / mean * 100, 1)

    # 예측: ③ '평균' 일별 시계열 → Prophet (forecast_prices 내부 캐시 공유)
    forecast_price = None
    forecast_pct = None
    fc_series = [(p.obs_date, p.price) for p in avg_points]
    if len(fc_series) >= 3:
        fc, _ = forecast_prices(fc_series, _FORECAST_DAYS)
        if fc:
            forecast_price = fc[-1].yhat
            forecast_pct = _pct(forecast_price, fc_series[-1][1])

    return ShippingFeatures(
        crop_name=_display_name(item_name, kind_name) or (recent.item_name if recent else item_code),
        unit=unit,
        current_price=current,
        prev_price=prev,
        vs_prev_pct=vs_prev,
        vs_year_ago_pct=vs_year,
        vs_normal_pct=vs_normal,
        trend_pct=trend_pct,
        volatility_pct=volatility,
        period_high=trend.month_high if trend else (max(avg_series) if avg_series else None),
        period_low=trend.month_low if trend else (min(avg_series) if avg_series else None),
        direction=_direction(trend_pct, vs_prev),
        samples=len(avg_series),
        forecast_price=forecast_price,
        forecast_pct=forecast_pct,
        forecast_days=_FORECAST_DAYS,
    )
