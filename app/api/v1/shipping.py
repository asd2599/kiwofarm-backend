from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.core.shipping.advice import generate_shipping_advice
from app.core.shipping.features import build_shipping_features
from app.core.shipping.forecast import forecast_prices
from app.data import kamis, kamis_productno
from app.schemas.shipping import (
    ForecastResponse,
    PeerFarmStat,
    PriceTrendResponse,
    PricePoint,
    RecentPriceResponse,
    RegionOption,
    ShippingAdviceResponse,
    ShippingDashboard,
    ShippingDecision,
    ShippingRegions,
    TrendPoint,
)

_forecast_cache: dict[tuple, dict] = {}

router = APIRouter(prefix="/shipping", tags=["shipping"])

# 평균/예년 같은 합성 county 는 지역 선택지에서 제외하되 시계열 계산에서는 활용 가능.
_SYNTHETIC_COUNTIES = {"평균", "평년"}
_LOOKBACK_DAYS = 30  # 최근 거래일 N일치 → 지역 후보 확보 충분


def _display_name(item_name: str, kind_name: str) -> str:
    if not kind_name or kind_name == item_name:
        return item_name
    return f"{item_name} — {kind_name}"


def _build_regions(points: list[kamis.WholesalePoint]) -> list[RegionOption]:
    """county 별 최신 가격 + 표본 수. 표본 많은 순으로 정렬."""
    by_county = kamis.group_by_county(points)
    out: list[RegionOption] = []
    for county, series in by_county.items():
        if county in _SYNTHETIC_COUNTIES:
            continue
        latest = series[-1]
        market = Counter(p.market for p in series if p.market).most_common(1)
        out.append(
            RegionOption(
                county=county,
                market=market[0][0] if market else "",
                latest_price=latest.price,
                sample_count=len(series),
            )
        )
    out.sort(key=lambda r: (-r.sample_count, r.county))
    return out


def _to_series(points: list[kamis.WholesalePoint]) -> list[PricePoint]:
    return [
        PricePoint(date=p.obs_date, price=p.price, is_forecast=False)
        for p in points
    ]


def _forecast(series: list[PricePoint], horizon_days: int = 14) -> list[PricePoint]:
    """단순 추세 기반 예측 (Prophet 도입 전 placeholder).

    최근 5일 가격 평균 + 최근 7일 일평균 변화율을 선형 외삽.
    신뢰구간은 평균 가격의 ±8%로 보수 추정.
    """
    if not series:
        return []
    recent = series[-7:] if len(series) >= 7 else series
    avg = sum(p.price for p in recent) / len(recent)
    if len(recent) >= 2:
        daily_delta = (recent[-1].price - recent[0].price) / max(1, len(recent) - 1)
    else:
        daily_delta = 0
    band = max(150, int(avg * 0.08))

    last_date = series[-1].date
    last_price = series[-1].price
    out: list[PricePoint] = []
    for i in range(1, horizon_days + 1):
        d = last_date + timedelta(days=i)
        yhat = int(last_price + daily_delta * i)
        out.append(
            PricePoint(
                date=d,
                price=yhat,
                is_forecast=True,
                forecast_low=yhat - band,
                forecast_high=yhat + band,
            )
        )
    return out


def _decision(actual: list[PricePoint], forecast: list[PricePoint]) -> ShippingDecision:
    """오늘 vs 3일 후 단가 비교로 출하 시점 추천 (Prophet/우수농가 결합 전 단순화)."""
    today_price = actual[-1].price if actual else 0
    target = forecast[2] if len(forecast) >= 3 else (forecast[-1] if forecast else None)
    future_price = target.price if target else today_price

    delta = future_price - today_price
    delta_pct = (delta / today_price * 100) if today_price else 0.0

    def score(p: int, base: int) -> int:
        if not base:
            return 3
        ratio = p / base
        if ratio >= 1.05:
            return 5
        if ratio >= 1.02:
            return 4
        if ratio >= 0.98:
            return 3
        if ratio >= 0.95:
            return 2
        return 1

    base = today_price
    score_today = 3
    score_future = score(future_price, base)

    if delta_pct >= 3:
        rec = f"{(target.date - actual[-1].date).days if target else 3}일 후 출하 권장"
    elif delta_pct <= -3:
        rec = "오늘 출하 권장"
    else:
        rec = "현 시점 출하 또는 보관 모두 가능"

    return ShippingDecision(
        score_today=score_today,
        score_in_3d=score_future,
        price_today=today_price,
        price_in_3d=future_price,
        recommendation=rec,
        delta_pct=round(delta_pct, 1),
    )


@router.get("/forecast", response_model=None)
async def shipping_forecast_endpoint(
    category_code: str = Query(..., description="KAMIS 부류코드"),
    item_code: str = Query(...),
    kind_code: str = Query(""),
    item_name: str = Query(""),
    kind_name: str = Query(""),
    horizon: int = Query(7, ge=1, le=14),
) -> JSONResponse:
    """검색 작물의 도매가 예측 (③ 일별 시계열 → Prophet)."""
    crop_name = _display_name(item_name, kind_name)
    today = date.today()
    points = await kamis.fetch_wholesale_period(
        category_code=category_code,
        item_code=item_code,
        kind_code=kind_code,
        start=today - timedelta(days=90),
        end=today,
    )
    avg = kamis.group_by_county(points).get("평균") or []
    series = [(p.obs_date, p.price) for p in avg]

    if len(series) < 3:
        payload = ForecastResponse(
            found=False,
            crop_name=crop_name,
            message="예측에 필요한 도매가 데이터가 부족합니다.",
        )
        return JSONResponse(payload.model_dump(mode="json"))

    last_date = series[-1][0]
    cache_key = (item_code, kind_code, last_date.isoformat(), horizon)
    if cache_key in _forecast_cache:
        return JSONResponse(_forecast_cache[cache_key])

    fc, method = forecast_prices(series, horizon)
    rec = kamis_productno.find(item_code, kind_code)
    unit = (rec.get("unit") if rec else None) or None

    actual = [PricePoint(date=d, price=p, is_forecast=False) for d, p in series[-30:]]
    forecast_pts = [
        PricePoint(
            date=f.date,
            price=f.yhat,
            is_forecast=True,
            forecast_low=f.lower,
            forecast_high=f.upper,
        )
        for f in fc
    ]
    last = fc[-1] if fc else None
    payload = ForecastResponse(
        found=True,
        crop_name=crop_name or item_code,
        unit=unit,
        method=method,
        horizon_days=horizon,
        series=actual + forecast_pts,
        forecast_last=last.yhat if last else None,
        forecast_last_low=last.lower if last else None,
        forecast_last_high=last.upper if last else None,
    ).model_dump(mode="json")
    _forecast_cache[cache_key] = payload
    return JSONResponse(payload)


@router.get("/trend", response_model=None)
async def shipping_trend_endpoint(
    category_code: str = Query("", description="KAMIS 부류코드 (미사용, 일관성용)"),
    item_code: str = Query(...),
    kind_code: str = Query(""),
    item_name: str = Query(""),
    kind_name: str = Query(""),
) -> JSONResponse:
    """검색 작물의 최근 가격추이 (올해/작년/평년)."""
    crop_name = _display_name(item_name, kind_name)
    t = await kamis.fetch_price_trend(item_code=item_code, kind_code=kind_code)
    if t is None:
        payload = PriceTrendResponse(
            found=False,
            crop_name=crop_name,
            message="가격 추이 정보가 없는 작물입니다.",
        )
        return JSONResponse(payload.model_dump(mode="json"))

    payload = PriceTrendResponse(
        found=True,
        crop_name=crop_name or t.item_name,
        unit=t.unit,
        rank=t.rank,
        points=[TrendPoint(**p) for p in t.points],
        latest=t.latest,
        year_ago=t.year_ago,
        normal=t.normal,
        month_high=t.month_high,
        month_low=t.month_low,
    )
    return JSONResponse(payload.model_dump(mode="json"))


@router.get("/advice", response_model=None)
async def shipping_advice_endpoint(
    category_code: str = Query(..., description="KAMIS 부류코드"),
    item_code: str = Query(...),
    kind_code: str = Query(""),
    item_name: str = Query(""),
    kind_name: str = Query(""),
) -> JSONResponse:
    """검색 작물의 KAMIS 3종 지표 → AI 출하 조언."""
    feats = await build_shipping_features(
        category_code=category_code,
        item_code=item_code,
        kind_code=kind_code,
        item_name=item_name,
        kind_name=kind_name,
    )
    if feats.current_price is None:
        payload = ShippingAdviceResponse(
            found=False,
            crop_name=feats.crop_name,
            advice="",
            source="none",
            message="도매가 데이터가 없어 조언을 생성할 수 없습니다.",
        )
        return JSONResponse(payload.model_dump(mode="json"))

    advice, source = await generate_shipping_advice(feats)
    payload = ShippingAdviceResponse(
        found=True,
        crop_name=feats.crop_name,
        advice=advice,
        source=source,
        current_price=feats.current_price,
        unit=feats.unit,
        vs_prev_pct=feats.vs_prev_pct,
        vs_year_ago_pct=feats.vs_year_ago_pct,
        vs_normal_pct=feats.vs_normal_pct,
        trend_pct=feats.trend_pct,
        volatility_pct=feats.volatility_pct,
        direction=feats.direction,
        forecast_price=feats.forecast_price,
        forecast_pct=feats.forecast_pct,
        forecast_days=feats.forecast_days,
    )
    return JSONResponse(payload.model_dump(mode="json"))


@router.get("/recent", response_model=None)
async def recent_price_endpoint(
    category_code: str = Query(..., description="KAMIS 부류코드"),
    item_code: str = Query(...),
    kind_code: str = Query(""),
    item_name: str = Query(""),
    kind_name: str = Query(""),
) -> JSONResponse:
    """검색한 작물의 최근일자 도매가 1건."""
    crop_name = _display_name(item_name, kind_name)
    rp = await kamis.fetch_recent_price(
        category_code=category_code,
        item_code=item_code,
        kind_code=kind_code,
        product_cls_code="02",
    )
    if rp is None:
        payload = RecentPriceResponse(
            found=False,
            crop_name=crop_name,
            item_code=item_code,
            message="최근 도매가 정보가 없습니다.",
        )
        return JSONResponse(payload.model_dump(mode="json"))

    delta_pct = None
    if rp.prev_price:
        delta_pct = round((rp.price - rp.prev_price) / rp.prev_price * 100, 1)

    payload = RecentPriceResponse(
        found=True,
        crop_name=crop_name,
        item_code=rp.item_code,
        product_cls="도매" if rp.product_cls_code == "02" else "소매",
        kind_name=rp.kind_name,
        rank=rp.rank,
        unit=rp.unit,
        obs_date=rp.obs_date,
        price=rp.price,
        prev_price=rp.prev_price,
        delta_pct=delta_pct,
    )
    return JSONResponse(payload.model_dump(mode="json"))


@router.get("", response_model=None)
async def shipping_endpoint(
    category_code: str = Query(..., description="KAMIS 부류코드"),
    item_code: str = Query(...),
    kind_code: str = Query("00"),
    item_name: str = Query(...),
    kind_name: str = Query(""),
    region: str | None = Query(None, description="county명. 없으면 가용 지역만 반환"),
) -> JSONResponse:
    today = date.today()
    start = today - timedelta(days=_LOOKBACK_DAYS)
    points = await kamis.fetch_wholesale_period(
        category_code=category_code,
        item_code=item_code,
        kind_code=kind_code,
        start=start,
        end=today,
    )

    crop_name = _display_name(item_name, kind_name)
    crop_id = f"{item_code}-{kind_code}"
    regions = _build_regions(points)

    # 가용 지역이 0 또는 1이면 자동으로 그 지역(또는 전국 평균)으로 진입.
    if region is None:
        if len(regions) <= 1:
            region = regions[0].county if regions else "평균"
        else:
            payload = ShippingRegions(crop_id=crop_id, crop_name=crop_name, regions=regions)
            return JSONResponse(payload.model_dump(mode="json"))

    by_county = kamis.group_by_county(points)
    selected = by_county.get(region) or by_county.get("평균") or []
    if not selected:
        payload = ShippingRegions(
            crop_id=crop_id,
            crop_name=crop_name,
            regions=regions,
            note=f"{region} 지역 도매가 데이터가 없습니다.",
        )
        return JSONResponse(payload.model_dump(mode="json"))

    actual = _to_series(selected)
    forecast = _forecast(actual)
    decision = _decision(actual, forecast)
    market = Counter(p.market for p in selected if p.market).most_common(1)

    dashboard = ShippingDashboard(
        crop_id=crop_id,
        crop_name=crop_name,
        region=region,
        market=market[0][0] if market else "",
        updated_at=actual[-1].date,
        decision=decision,
        price_series=actual + forecast,
        peer=PeerFarmStat(
            region=region,
            total_farms=12,
            farms_aligned=9,
            note="우수농가 패턴 — 실데이터 연동 전 placeholder",
        ),
    )
    return JSONResponse(dashboard.model_dump(mode="json"))
