"""작목 추천 엔진.

TODO 실구현:
  - SmartFarmKorea 우수농가 112호 CSV 로드 (app.data.smartfarm)
  - KMA ASOS 지역 기후 매칭 (app.data.kma)
  - Nongsaro 작목 적합도 (app.data.nongsaro)
  - XGBoost 학습/추론 + 룰베이스 fallback
"""

from app.schemas.recommend import (
    CalendarMonth,
    CropRecommendationItem,
    OnboardingInput,
    RecommendationResponse,
)


def _cal(phases: dict[str, list[int]]) -> list[CalendarMonth]:
    out: list[CalendarMonth] = []
    for m in range(1, 13):
        phase = "rest"
        for p, months in phases.items():
            if m in months:
                phase = p
        out.append(CalendarMonth(month=m, phase=phase))  # type: ignore[arg-type]
    return out


_RETURNING_SEED: list[CropRecommendationItem] = [
    CropRecommendationItem(
        cropId="tomato",
        name="방울토마토",
        emoji="🍅",
        matchScore=94,
        difficulty=2,
        expectedRevenueManwon=4260,
        expectedNetManwon=2180,
        expectedYieldKg=9800,
        expectedDirectPriceWon=0,
        llmReason="옥천 평균기온·일조시간이 시설 토마토 최적범위에 있고, 수도권 도매가 최근 3년 안정세입니다.",
        tags=["시설재배", "수도권 출하 유리", "연중 출하"],
        calendar=_cal(
            {
                "rest": [12, 1],
                "seeding": [2, 3],
                "growing": [4, 5, 6, 11],
                "harvest": [7, 8, 9, 10],
            }
        ),
        peerFarms=12,
        peerAgreeRate=75,
        color="red",
    ),
    CropRecommendationItem(
        cropId="sweetpotato",
        name="고구마",
        emoji="🍠",
        matchScore=88,
        difficulty=1,
        expectedRevenueManwon=2980,
        expectedNetManwon=1620,
        expectedYieldKg=6500,
        expectedDirectPriceWon=0,
        llmReason="노지 작목 중 초보자 진입장벽이 가장 낮고, 옥천 토양 적합도가 높습니다.",
        tags=["노지재배", "초보 친화", "저장 가능"],
        calendar=_cal(
            {
                "rest": [12, 1, 2],
                "seeding": [3, 4],
                "growing": [5, 6, 7, 8],
                "harvest": [9, 10, 11],
            }
        ),
        peerFarms=18,
        peerAgreeRate=67,
        color="orange",
    ),
    CropRecommendationItem(
        cropId="blueberry",
        name="블루베리",
        emoji="🫐",
        matchScore=81,
        difficulty=3,
        expectedRevenueManwon=5840,
        expectedNetManwon=2540,
        expectedYieldKg=1800,
        expectedDirectPriceWon=0,
        llmReason="초기 묘목 투자가 필요하지만 단가가 높고 직거래·체험농장 수요가 큽니다.",
        tags=["고소득 작물", "체험농장 가능", "3년 후 안정"],
        calendar=_cal(
            {
                "rest": [11, 12, 1, 2],
                "seeding": [3],
                "growing": [4, 5, 9, 10],
                "harvest": [6, 7, 8],
            }
        ),
        peerFarms=7,
        peerAgreeRate=58,
        color="indigo",
    ),
]


_WEEKEND_SEED: list[CropRecommendationItem] = [
    CropRecommendationItem(
        cropId="tomato",
        name="방울토마토",
        emoji="🍅",
        matchScore=92,
        difficulty=2,
        expectedRevenueManwon=0,
        expectedNetManwon=0,
        expectedYieldKg=24,
        expectedDirectPriceWon=12000,
        llmReason="주말 1회 방문으로 충분히 관리 가능한 작목.",
        tags=["주1회 관리", "8주 연속 수확", "직거래 가능"],
        calendar=_cal(
            {
                "rest": [12, 1, 2],
                "seeding": [3, 4],
                "growing": [5, 6, 11],
                "harvest": [7, 8, 9, 10],
            }
        ),
        peerFarms=22,
        peerAgreeRate=82,
        color="red",
    ),
    CropRecommendationItem(
        cropId="lettuce",
        name="상추",
        emoji="🥬",
        matchScore=87,
        difficulty=1,
        expectedRevenueManwon=0,
        expectedNetManwon=0,
        expectedYieldKg=18,
        expectedDirectPriceWon=6000,
        llmReason="발아 빠르고 실패율 가장 낮은 작목.",
        tags=["초보 친화", "연 2회 수확", "직거래 인기"],
        calendar=_cal(
            {
                "rest": [12, 1, 2, 7, 8],
                "seeding": [3, 9],
                "growing": [4, 10],
                "harvest": [5, 6, 11],
            }
        ),
        peerFarms=31,
        peerAgreeRate=90,
        color="orange",
    ),
    CropRecommendationItem(
        cropId="pepper",
        name="청양고추",
        emoji="🌶️",
        matchScore=79,
        difficulty=2,
        expectedRevenueManwon=0,
        expectedNetManwon=0,
        expectedYieldKg=9,
        expectedDirectPriceWon=18000,
        llmReason="한 그루당 수확량이 많고 보관·건조도 가능.",
        tags=["장기 수확", "보관 용이", "이웃나눔"],
        calendar=_cal(
            {
                "rest": [12, 1, 2, 3],
                "seeding": [4],
                "growing": [5, 6, 7],
                "harvest": [8, 9, 10, 11],
            }
        ),
        peerFarms=14,
        peerAgreeRate=71,
        color="indigo",
    ),
]


def recommend(input_: OnboardingInput) -> RecommendationResponse:
    """주어진 사용자 입력에 대해 TOP-3 작목 추천.

    현재 구현: 모드별 시드 데이터 반환. XGBoost 모델 적용 전 placeholder.
    """
    items = _RETURNING_SEED if input_.mode == "returning" else _WEEKEND_SEED
    return RecommendationResponse(mode=input_.mode, items=items)
