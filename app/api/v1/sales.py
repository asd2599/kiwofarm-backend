"""판매 도우미 API — 가까운 직매장 추천 + 채널별 수익 비교."""

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.core.sales.recommend import recommend_channels
from app.core.sales.revenue import compare_channels
from app.data import localfood
from app.schemas.sales import (
    ChannelOut,
    CompareResponse,
    MarketOut,
    NearbyResponse,
    PlaceOut,
    RecommendChannelOut,
    RecommendResponse,
    StoreOut,
)

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get("/nearby", response_model=None)
async def nearby_stores_endpoint(
    lat: float | None = Query(None, description="판매 위치 위도"),
    lng: float | None = Query(None, description="판매 위치 경도"),
    address: str | None = Query(None, description="좌표 대신 주소(런타임 지오코딩)"),
    limit: int = Query(5, ge=1, le=20),
) -> JSONResponse:
    """판매 위치 기준 가까운 로컬푸드 직매장."""
    origin_label: str | None = None
    if lat is None or lng is None:
        if not address:
            payload = NearbyResponse(
                found=False, message="좌표(lat,lng) 또는 주소(address)가 필요합니다."
            )
            return JSONResponse(payload.model_dump(mode="json"))
        geo = await localfood.geocode_address(address)
        if geo is None:
            payload = NearbyResponse(
                found=False, message=f"'{address}' 위치를 찾지 못했습니다."
            )
            return JSONResponse(payload.model_dump(mode="json"))
        lat, lng, origin_label = geo

    stores = localfood.nearest_stores(lat, lng, limit)
    payload = NearbyResponse(
        found=bool(stores),
        origin_lat=lat,
        origin_lng=lng,
        origin_label=origin_label,
        stores=[StoreOut(**s) for s in stores],
        message=None if stores else "추천할 직매장이 없습니다.",
    )
    return JSONResponse(payload.model_dump(mode="json"))


@router.get("/compare", response_model=None)
async def compare_channels_endpoint(
    category_code: str = Query(..., description="KAMIS 부류코드"),
    item_code: str = Query(...),
    kind_code: str = Query(""),
    item_name: str = Query(""),
    kind_name: str = Query(""),
    amount: float | None = Query(None, gt=0, description="판매량(도매 단위에 따라 kg 또는 개)"),
) -> JSONResponse:
    """직매장 직거래 vs 도매시장 출하 예상 실수령액 비교.

    입력 단위(무게/갯수)는 도매가 단위로 자동 결정된다(응답 input_mode/amount_unit).
    """
    crop_name = item_name + (f" — {kind_name}" if kind_name and kind_name != item_name else "")
    result = await compare_channels(
        category_code=category_code,
        item_code=item_code,
        kind_code=kind_code,
        crop_name=crop_name or item_code,
        amount=amount,
    )
    payload = CompareResponse(
        found=result.found,
        crop_name=result.crop_name,
        amount=result.amount,
        amount_unit=result.amount_unit,
        input_mode=result.input_mode,
        obs_date=result.obs_date,
        channels=[ChannelOut(**c.__dict__) for c in result.channels],
        best_key=result.best_key,
        delta_net=result.delta_net,
        message=result.message,
    )
    return JSONResponse(payload.model_dump(mode="json"))


@router.get("/recommend", response_model=None)
async def recommend_endpoint(
    category_code: str = Query(..., description="KAMIS 부류코드"),
    item_code: str = Query(...),
    kind_code: str = Query(""),
    item_name: str = Query(""),
    kind_name: str = Query(""),
    amount: float | None = Query(None, gt=0, description="판매량(kg 또는 개)"),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    address: str | None = Query(None, description="좌표 대신 주소(런타임 지오코딩)"),
) -> JSONResponse:
    """판매 위치 기준 직거래 vs 도매시장 추천 (가격 + 운송비 + AI)."""
    crop_name = item_name + (f" — {kind_name}" if kind_name and kind_name != item_name else "")
    origin_label: str | None = None
    if lat is None or lng is None:
        if not address:
            payload = RecommendResponse(
                found=False, crop_name=crop_name or item_code,
                message="좌표(lat,lng) 또는 주소(address)가 필요합니다.",
            )
            return JSONResponse(payload.model_dump(mode="json"))
        geo = await localfood.geocode_address(address)
        if geo is None:
            payload = RecommendResponse(
                found=False, crop_name=crop_name or item_code,
                message=f"'{address}' 위치를 찾지 못했습니다.",
            )
            return JSONResponse(payload.model_dump(mode="json"))
        lat, lng, origin_label = geo

    r = await recommend_channels(
        category_code=category_code, item_code=item_code, kind_code=kind_code,
        crop_name=crop_name or item_code, amount=amount,
        lat=lat, lng=lng, origin_label=origin_label,
    )

    payload = RecommendResponse(
        found=r.found,
        crop_name=r.crop_name,
        amount=r.amount,
        amount_unit=r.amount_unit,
        input_mode=r.input_mode,
        obs_date=r.obs_date,
        origin_lat=r.origin_lat,
        origin_lng=r.origin_lng,
        origin_label=r.origin_label,
        channels=[
            RecommendChannelOut(
                key=c.key, label=c.label, net=c.net, unit_price=c.unit_price,
                source_price=c.source_price, source_unit=c.source_unit,
                commission_pct=c.commission_pct, note=c.note, estimated=c.estimated,
                place=PlaceOut(**c.place.__dict__) if c.place else None,
                transport_cost=c.transport_cost, net_after=c.net_after,
            )
            for c in r.channels
        ],
        best_key=r.best_key,
        delta_net_after=r.delta_net_after,
        per_km_won=r.per_km_won,
        advice=r.advice,
        advice_source=r.advice_source,
        nearby_direct=[StoreOut(**s) for s in r.nearby_direct],
        nearby_wholesale=[MarketOut(**m) for m in r.nearby_wholesale],
        message=r.message,
    )
    return JSONResponse(payload.model_dump(mode="json"))
