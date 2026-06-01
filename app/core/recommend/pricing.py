"""premium 작목 KAMIS 도매 단가 산출 (수익 모델 Stage 1).

수익 = 단가 × 수율 × 면적 에서 '단가' 축을 담당한다.
KAMIS 클라이언트(app/data/kamis)는 팀원 소유 → 호출만 하고 수정하지 않는다.
가격 단위(5kg 등) → 원/kg 정규화도 KAMIS 쪽(팀원)에서 처리하므로, 본 모듈은
반환 price 를 이미 원/kg 로 가정한다. (정규화 전이면 raw_unit 배수만큼 차이남)

산출 방식:
  1) 최근 _LOOKBACK_DAYS 일 '평균'(전국 도매 평균) 일별 시계열 조회
  2) 작목 출하월(engine 카탈로그 harvest)에 해당하는 날만 추려 평균 → 시즈널 보정
     (예: 딸기를 6월 비수기 단가로 잡으면 왜곡되므로 수확월 1~6월만)
  3) 출하월 표본이 없으면 전체 평균, KAMIS 실패/빈값이면 None (호출측이 카탈로그 폴백)
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from app.data import kamis

# crop_id -> (category_code, item_code, kind_code)
# ※ 완숙토마토는 KAMIS 일반 '토마토'(225) 에 해당 (방울토마토 422 와 별개)
KAMIS_CODE: dict[str, tuple[str, str, str]] = {
    "tomato": ("200", "225", "00"),
    "strawberry": ("200", "226", "00"),
    "paprika": ("200", "256", "00"),
}

_LOOKBACK_DAYS = 365
_cache: dict[str, UnitPrice] = {}


@dataclass(frozen=True)
class UnitPrice:
    crop_id: str
    won_per_kg: int  # 팀원 정규화 후 원/kg 가정
    basis: str  # "출하월(7·8·9·10월) 평균" 등 산출 근거
    sample_points: int  # 평균에 쓰인 일자 수
    raw_unit: str | None  # KAMIS 가 알려준 단위(투명성·정규화 검증용)
    source: str  # "KAMIS 도매" | "KAMIS 소매(폴백)"


def harvest_months_of(crop_id: str) -> list[int]:
    """engine 카탈로그에서 작목 출하월(harvest) 추출. 지연 import(순환 방지)."""
    from app.core.recommend.engine import CATALOG

    for c in CATALOG:
        if c.crop_id == crop_id:
            return sorted(c.calendar.get("harvest", []))
    return []


async def get_unit_price(
    crop_id: str, harvest_months: list[int] | None = None,
) -> UnitPrice | None:
    """작목 KAMIS 도매 평균 단가(원/kg). 미지원 작목·KAMIS 실패 시 None."""
    code = KAMIS_CODE.get(crop_id)
    if code is None:
        return None
    if crop_id in _cache:
        return _cache[crop_id]

    if harvest_months is None:
        harvest_months = harvest_months_of(crop_id)

    category, item, kind = code
    today = date.today()
    try:
        points = await kamis.fetch_wholesale_period(
            category_code=category,
            item_code=item,
            kind_code=kind,
            start=today - timedelta(days=_LOOKBACK_DAYS),
            end=today,
        )
    except Exception:
        return None

    avg = kamis.group_by_county(points).get("평균", [])
    if not avg:
        return None

    cls = getattr(avg[0], "price_cls", None)
    source = "KAMIS 소매(폴백)" if cls == "01" else "KAMIS 도매"

    # WholesalePoint 에는 단위가 없어 trend 에서 best-effort 로 가져온다.
    # 팀원 정규화 후엔 "kg" 류, 정규화 전이면 "5kg" 등 → 정규화 적용 확인 신호.
    raw_unit: str | None = None
    try:
        tr = await kamis.fetch_price_trend(item, kind)
        raw_unit = getattr(tr, "unit", None) if tr else None
    except Exception:
        raw_unit = None

    seasonal = [p.price for p in avg if harvest_months and p.obs_date.month in harvest_months]
    if seasonal:
        label = "·".join(str(m) for m in harvest_months)
        basis = f"출하월({label}월) 평균"
        prices = seasonal
    else:
        basis = "연간 평균" if not harvest_months else "연간 평균(출하월 표본 없음)"
        prices = [p.price for p in avg]

    result = UnitPrice(
        crop_id=crop_id,
        won_per_kg=round(statistics.mean(prices)),
        basis=basis,
        sample_points=len(prices),
        raw_unit=raw_unit,
        source=source,
    )
    _cache[crop_id] = result
    return result
