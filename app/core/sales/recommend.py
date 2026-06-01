"""판매 위치 기반 채널 추천 — 가격 + 운송비 + AI.

지역을 받아서
  1) 채널별 가격 실수령(compare_channels) 계산
  2) 가까운 직매장(localfood)·도매시장(wholesale_markets) 탐색
  3) 거리 기반 왕복 운송비 차감 → 채널별 '최종 실수령'
  4) GPT-4o 로 어디서 파는 게 유리한지 추천
을 한 번에 묶는다.

운송비는 1톤 트럭 왕복 연료·유지비를 거리로 단순 추정한 참고 수치다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.sales.advice import ChannelFeature, SalesFeatures, generate_sales_advice
from app.core.sales.revenue import compare_channels
from app.data import localfood, wholesale_markets

PER_KM_WON = 600  # 1톤 트럭 km당 연료·유지비 추정
ROUND_TRIP = 2    # 왕복


def transport_cost(distance_km: float | None) -> int | None:
    if distance_km is None:
        return None
    return int(round(distance_km * ROUND_TRIP * PER_KM_WON))


@dataclass
class Place:
    kind: str            # 'direct' | 'wholesale'
    name: str
    distance_km: float
    sido: str = ""
    address: str = ""
    phone: str = ""
    lat: float | None = None
    lng: float | None = None


@dataclass
class RecommendChannel:
    key: str
    label: str
    net: int | None              # 가격 실수령 (운송 전)
    unit_price: float | None
    source_price: int
    source_unit: str
    commission_pct: float
    note: str
    estimated: bool
    place: Place | None
    transport_cost: int | None
    net_after: int | None        # net - transport


@dataclass
class RecommendResult:
    found: bool
    crop_name: str
    amount: float | None
    amount_unit: str
    input_mode: str
    obs_date: str | None
    origin_lat: float | None
    origin_lng: float | None
    origin_label: str | None
    channels: list[RecommendChannel]
    best_key: str | None
    delta_net_after: int | None
    per_km_won: int
    advice: str
    advice_source: str
    nearby_direct: list[dict]
    nearby_wholesale: list[dict]
    message: str | None = None


def _direct_place(stores: list[dict]) -> Place | None:
    if not stores:
        return None
    s = stores[0]
    return Place(
        kind="direct", name=s["name"], distance_km=s["distance_km"],
        sido=s.get("sido", ""), address=s.get("address", ""),
        phone=s.get("phone", ""), lat=s.get("lat"), lng=s.get("lng"),
    )


def _wholesale_place(markets: list[dict]) -> Place | None:
    if not markets:
        return None
    m = markets[0]
    return Place(
        kind="wholesale", name=m["name"], distance_km=m["distance_km"],
        sido=m.get("sido", ""), lat=m.get("lat"), lng=m.get("lng"),
    )


async def recommend_channels(
    *,
    category_code: str,
    item_code: str,
    kind_code: str,
    crop_name: str,
    amount: float | None,
    lat: float,
    lng: float,
    origin_label: str | None,
) -> RecommendResult:
    cmp = await compare_channels(
        category_code=category_code, item_code=item_code,
        kind_code=kind_code, crop_name=crop_name, amount=amount,
    )

    nearby_direct = localfood.nearest_stores(lat, lng, 5)
    nearby_wholesale = wholesale_markets.nearest_markets(lat, lng, 5)
    places = {"direct": _direct_place(nearby_direct), "wholesale": _wholesale_place(nearby_wholesale)}

    if not cmp.found:
        return RecommendResult(
            found=False, crop_name=cmp.crop_name, amount=amount,
            amount_unit=cmp.amount_unit, input_mode=cmp.input_mode, obs_date=None,
            origin_lat=lat, origin_lng=lng, origin_label=origin_label,
            channels=[], best_key=None, delta_net_after=None, per_km_won=PER_KM_WON,
            advice="", advice_source="none",
            nearby_direct=nearby_direct, nearby_wholesale=nearby_wholesale,
            message=cmp.message,
        )

    channels: list[RecommendChannel] = []
    for ch in cmp.channels:
        place = places.get(ch.key)
        tcost = transport_cost(place.distance_km) if place else None
        net_after = (ch.net - tcost) if (ch.net is not None and tcost is not None) else ch.net
        channels.append(RecommendChannel(
            key=ch.key, label=ch.label, net=ch.net, unit_price=ch.unit_price,
            source_price=ch.source_price, source_unit=ch.source_unit,
            commission_pct=ch.commission_pct, note=ch.note, estimated=ch.estimated,
            place=place, transport_cost=tcost, net_after=net_after,
        ))

    rated = [c for c in channels if c.net_after is not None]
    rated.sort(key=lambda c: c.net_after or 0, reverse=True)
    best_key = rated[0].key if rated else None
    delta_after = (rated[0].net_after - rated[1].net_after) if len(rated) >= 2 else None

    feats = SalesFeatures(
        crop_name=cmp.crop_name, amount=amount, amount_unit=cmp.amount_unit,
        channels=[
            ChannelFeature(
                key=c.key, label=c.label, net=c.net,
                place_name=c.place.name if c.place else None,
                distance_km=c.place.distance_km if c.place else None,
                transport_cost=c.transport_cost, net_after=c.net_after,
            )
            for c in channels
        ],
        best_key=best_key, delta_after=delta_after,
    )
    advice, source = await generate_sales_advice(feats)

    return RecommendResult(
        found=True, crop_name=cmp.crop_name, amount=amount,
        amount_unit=cmp.amount_unit, input_mode=cmp.input_mode, obs_date=cmp.obs_date,
        origin_lat=lat, origin_lng=lng, origin_label=origin_label,
        channels=channels, best_key=best_key, delta_net_after=delta_after,
        per_km_won=PER_KM_WON, advice=advice, advice_source=source,
        nearby_direct=nearby_direct, nearby_wholesale=nearby_wholesale,
    )
