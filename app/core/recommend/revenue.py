"""premium 작목 수익(매출·순이익) KAMIS 단가 결합 (수익 모델 Stage 3 골격).

  매출(만원) = KAMIS 출하월 단가(원/kg) × 표준수율(kg/평) × 면적(평) / 10000
  순이익(만원) = 매출 × 소득률(카탈로그 net/revenue 비율 재사용)

귀농(returning) 모드의 premium 작목만 engine 카탈로그 추정 매출을 KAMIS 실단가
기반으로 덮어쓴다. 주말농장 모드(수확량·직거래 단가 표시)는 손대지 않는다.

단가 단위(원/kg) 정규화는 KAMIS(팀원)에서 보장 → 정규화 적용 시 자동 정상화.
정규화 전(예: 5kg 단위)이면 매출이 그 배수만큼 크게 나오며, 팀원 정규화가 들어오면
본 모듈 수정 없이 정상값이 된다. KAMIS 실패/미지원 작목은 카탈로그 값 유지(폴백).

수율은 본래 스마트팜 우수농가 실측(prddatarqst.outtrn, Kg/3.3㎡=kg/평)을 쓸 자리지만
그 API 가 장애라, 당분간 우수농가 수준 표준 단수 상수를 쓴다.
TODO: 스마트팜 API 복구 시 STD_YIELD → 로스터 outtrn 평균으로 교체.
"""

from __future__ import annotations

import asyncio

from app.core.recommend.peer_match import to_pyeong
from app.core.recommend.pricing import get_unit_price
from app.schemas.recommend import CropRecommendationItem, OnboardingInput

# 우수농가 시설 표준 단수 (kg/평·년). 농촌진흥청 시설 단수 환산 (10a≈302평):
#   완숙토마토 ~10t/10a · 딸기(설향) ~4.5t/10a · 파프리카 ~11.5t/10a
STD_YIELD_KG_PER_PYEONG: dict[str, float] = {
    "tomato": 33.0,
    "strawberry": 15.0,
    "paprika": 38.0,
}


async def attach_revenue(
    items: list[CropRecommendationItem], input_: OnboardingInput
) -> list[CropRecommendationItem]:
    """귀농 premium 작목의 매출·순이익을 KAMIS 단가 기반으로 재계산한 새 리스트 반환."""
    if input_.mode != "returning":
        return items

    area_pyeong = to_pyeong(input_.area, input_.areaUnit)

    # premium & 수율 보유 작목만 단가 동시 조회 (나머지는 카탈로그 유지)
    idx = [
        i for i, it in enumerate(items)
        if it.tier == "premium" and it.cropId in STD_YIELD_KG_PER_PYEONG
    ]
    prices = await asyncio.gather(*(get_unit_price(items[i].cropId) for i in idx))
    price_by_idx = dict(zip(idx, prices, strict=True))

    out: list[CropRecommendationItem] = []
    for i, it in enumerate(items):
        up = price_by_idx.get(i)
        if up is None or it.expectedRevenueManwon <= 0:
            out.append(it)
            continue

        yield_kg = STD_YIELD_KG_PER_PYEONG[it.cropId]
        revenue = round(up.won_per_kg * yield_kg * area_pyeong / 10_000)
        # 소득률은 카탈로그가 이미 보정한 net/revenue 비율을 그대로 재사용.
        margin = it.expectedNetManwon / it.expectedRevenueManwon
        net = round(revenue * margin)

        basis = (
            f"매출 = {up.source} {up.basis} {up.won_per_kg:,}원/kg "
            f"× 표준수율 {yield_kg:g}kg/평 × {round(area_pyeong):,}평"
        )
        out.append(
            it.model_copy(
                update={
                    "expectedRevenueManwon": revenue,
                    "expectedNetManwon": net,
                    "revenueBasis": basis,
                }
            )
        )
    return out
