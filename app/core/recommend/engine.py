"""작목 추천 엔진 (룰베이스).

지역 기후(광역시·도별 연평균기온·서리위험) + 작목별 재배 적온/시설/자본 요건을
점수화해 TOP-3 작목을 가려낸다. 데이터 소스(SmartFarmKorea CSV / KMA ASOS /
Nongsaro)가 연결되기 전까지의 룰베이스 구현이며, 이후 XGBoost 추론으로 교체한다.

TODO 실구현:
  - SmartFarmKorea 우수농가 112호 CSV → 작목·지역·시설별 실측 수익/노동
  - KMA ASOS 지역 기후 매칭 (정적 PROVINCE_CLIMATE 대체)
  - Nongsaro 작목 적합도/병해충
  - XGBoost 학습/추론 + 본 룰베이스 fallback
"""

from dataclasses import dataclass, field

from app.schemas.recommend import (
    CalendarMonth,
    CropRecommendationItem,
    OnboardingInput,
    RecommendationResponse,
)


# ───────────────────────── 지역 기후 ─────────────────────────


@dataclass(frozen=True)
class RegionClimate:
    """광역시·도 단위 기후 요약 (데모용 정적값)."""

    annual_temp: float  # 연평균기온(℃)
    frost_risk: str  # low | medium | high
    note: str  # 추천 이유에 쓰는 한 줄 기후 설명


PROVINCE_CLIMATE: dict[str, RegionClimate] = {
    "제주특별자치도": RegionClimate(16.8, "low", "온화한 해양성 기후로 서리 피해가 거의 없어"),
    "부산광역시": RegionClimate(15.0, "low", "겨울이 따뜻한 남부 해안 기후라"),
    "울산광역시": RegionClimate(14.6, "low", "남동부 온난 기후로"),
    "대구광역시": RegionClimate(14.5, "low", "여름이 덥고 일조량이 풍부한 내륙 분지 기후라"),
    "전라남도": RegionClimate(14.4, "low", "겨울이 포근한 남부 기후로"),
    "경상남도": RegionClimate(14.3, "low", "따뜻한 남부 기후로"),
    "광주광역시": RegionClimate(14.2, "low", "온난한 남부 기후라"),
    "전북특별자치도": RegionClimate(13.4, "medium", "사계절이 뚜렷한 중남부 기후로"),
    "대전광역시": RegionClimate(13.4, "medium", "중부 내륙 기후로"),
    "경상북도": RegionClimate(13.0, "medium", "일교차가 큰 내륙 기후라"),
    "충청남도": RegionClimate(13.0, "medium", "중서부 평야 기후로"),
    "세종특별자치시": RegionClimate(12.9, "medium", "중부 내륙 기후로"),
    "서울특별시": RegionClimate(12.8, "medium", "도시 열섬이 있는 중부 기후라"),
    "충청북도": RegionClimate(12.5, "medium", "일교차가 큰 중부 내륙 기후로"),
    "인천광역시": RegionClimate(12.5, "medium", "서해안 해양성 기후로"),
    "경기도": RegionClimate(12.3, "medium", "중부 기후로"),
    "강원특별자치도": RegionClimate(10.8, "high", "서늘한 고랭지 기후로 서리 기간이 길어"),
}

DEFAULT_CLIMATE = RegionClimate(12.8, "medium", "중부 내륙 기후로")


def _resolve_climate(input_: OnboardingInput) -> tuple[str, RegionClimate]:
    """입력의 province(없으면 region 부분일치)로 기후를 찾는다."""
    if input_.province and input_.province in PROVINCE_CLIMATE:
        return input_.province, PROVINCE_CLIMATE[input_.province]
    # province 미전달 시: region 문자열이 province 명을 포함하는지 느슨히 매칭
    for prov, clim in PROVINCE_CLIMATE.items():
        if prov.startswith(input_.region) or input_.region in prov:
            return prov, clim
    return "중부", DEFAULT_CLIMATE


# ───────────────────────── 작목 카탈로그 ─────────────────────────


@dataclass(frozen=True)
class CropProfile:
    crop_id: str
    name: str
    emoji: str
    difficulty: int
    temp_lo: float  # 재배 적온 하한(연평균기온 기준)
    temp_hi: float  # 재배 적온 상한
    needs_facility: bool  # 시설(하우스) 재배가 유리한 작목
    perennial: bool  # 다년생(서리 민감·초기투자 큼)
    weekend_ok: bool  # 주말농장(소규모·단년) 적합 여부
    capital_manwon: int  # 귀농 초기 자본 기준
    revenue_manwon: int  # 귀농 기준 ~300평 연매출
    net_manwon: int  # 귀농 기준 연순이익
    yield_kg: int  # 주말농장 소규모 연수확량
    direct_price_won: int  # 주말농장 직거래 단가(원/kg)
    tags: list[str]
    blurb: str  # 작목 특성 한 줄(추천 이유용)
    calendar: dict[str, list[int]] = field(default_factory=dict)


CATALOG: list[CropProfile] = [
    CropProfile(
        "tomato", "방울토마토", "🍅", 2, 12.0, 16.5, True, False, True,
        2500, 4260, 2180, 24, 12000,
        ["시설재배", "수도권 출하 유리", "연중 출하"],
        "수도권 도매가가 안정적이고 시설에서 연중 출하가 가능한",
        {"rest": [12, 1], "seeding": [2, 3], "growing": [4, 5, 6, 11], "harvest": [7, 8, 9, 10]},
    ),
    CropProfile(
        "sweetpotato", "고구마", "🍠", 1, 13.0, 17.0, False, False, True,
        800, 2980, 1620, 30, 5000,
        ["노지재배", "초보 친화", "저장 가능"],
        "노지에서 자본·노동 부담이 가장 낮은 초보 친화",
        {"rest": [12, 1, 2], "seeding": [3, 4], "growing": [5, 6, 7, 8], "harvest": [9, 10, 11]},
    ),
    CropProfile(
        "blueberry", "블루베리", "🫐", 3, 10.5, 14.5, False, True, False,
        4000, 5840, 2540, 0, 0,
        ["고소득 작물", "체험농장 가능", "3년 후 안정"],
        "단가가 높고 직거래·체험농장 수요가 큰",
        {"rest": [11, 12, 1, 2], "seeding": [3], "growing": [4, 5, 9, 10], "harvest": [6, 7, 8]},
    ),
    CropProfile(
        "strawberry", "딸기", "🍓", 3, 10.0, 14.5, True, False, True,
        3500, 5200, 2600, 15, 20000,
        ["시설재배", "고단가", "체험·직거래 인기"],
        "겨울~봄 시설재배 단가가 높고 체험 수요가 큰",
        {"rest": [7, 8], "seeding": [9], "growing": [10, 11, 12], "harvest": [1, 2, 3, 4, 5, 6]},
    ),
    CropProfile(
        "paprika", "파프리카", "🫑", 4, 13.0, 16.5, True, False, False,
        6000, 6800, 2900, 0, 0,
        ["스마트팜", "수출 작목", "고소득"],
        "스마트팜 기반 수출·고소득이 가능하지만 초기투자가 큰",
        {"rest": [12, 1], "seeding": [2], "growing": [3, 4, 11], "harvest": [5, 6, 7, 8, 9, 10]},
    ),
    CropProfile(
        "pepper", "청양고추", "🌶️", 2, 13.0, 17.0, False, False, True,
        700, 3200, 1500, 9, 18000,
        ["노지재배", "장기 수확", "보관·가공 용이"],
        "한 그루 수확량이 많고 보관·건조 가공까지 되는",
        {"rest": [12, 1, 2, 3], "seeding": [4], "growing": [5, 6, 7], "harvest": [8, 9, 10, 11]},
    ),
    CropProfile(
        "lettuce", "상추", "🥬", 1, 9.0, 17.0, False, False, True,
        500, 2200, 1100, 18, 6000,
        ["초보 친화", "연 2회 수확", "직거래 인기"],
        "발아가 빠르고 실패율이 가장 낮은 초보 친화",
        {"rest": [12, 1, 2, 7, 8], "seeding": [3, 9], "growing": [4, 10], "harvest": [5, 6, 11]},
    ),
    CropProfile(
        "citrus", "감귤", "🍊", 3, 15.5, 19.0, False, True, False,
        4500, 4800, 2300, 0, 0,
        ["남부 특화", "다년생 과수", "직거래·관광"],
        "따뜻한 남부에서만 노지재배가 되는 다년생 과수",
        {"rest": [2, 3], "growing": [4, 5, 6, 7, 8, 9], "harvest": [10, 11, 12, 1]},
    ),
    CropProfile(
        "persimmon", "단감", "🟠", 3, 13.5, 16.5, False, True, False,
        3000, 3600, 1700, 0, 0,
        ["남부 특화", "다년생 과수", "저장 유리"],
        "남부 온난지에서 품질이 좋게 나오는 다년생 과수",
        {"rest": [12, 1, 2, 3], "growing": [4, 5, 6, 7, 8, 9], "harvest": [10, 11]},
    ),
    CropProfile(
        "apple", "사과", "🍎", 3, 9.0, 13.5, False, True, False,
        4000, 5000, 2400, 0, 0,
        ["냉량지 특화", "다년생 과수", "고소득"],
        "일교차가 큰 서늘한 지역에서 당도가 높게 나오는 과수",
        {"rest": [12, 1, 2, 3], "growing": [4, 5, 6, 7, 8], "harvest": [9, 10, 11]},
    ),
    CropProfile(
        "grape", "포도", "🍇", 3, 11.5, 15.0, False, True, False,
        4200, 5600, 2700, 0, 0,
        ["다년생 과수", "고단가", "체험농장 가능"],
        "단가가 높고 체험농장 연계가 좋은 다년생 과수",
        {"rest": [11, 12, 1, 2, 3], "growing": [4, 5, 6, 7], "harvest": [8, 9, 10]},
    ),
    CropProfile(
        "potato", "감자", "🥔", 1, 8.5, 13.5, False, False, True,
        600, 2400, 1200, 40, 4000,
        ["냉량지 적합", "초보 친화", "저장 가능"],
        "서늘한 기후에 잘 맞고 저장·관리가 쉬운 초보 친화",
        {"rest": [11, 12, 1, 2], "seeding": [3, 4], "growing": [5], "harvest": [6, 7]},
    ),
    CropProfile(
        "corn", "옥수수", "🌽", 1, 11.0, 15.5, False, False, True,
        400, 1800, 900, 35, 3000,
        ["노지재배", "초보 친화", "단기 재배"],
        "단기간에 수확하는 가장 손쉬운 노지",
        {"rest": [1, 2, 3, 11, 12], "seeding": [4], "growing": [5, 6], "harvest": [7, 8]},
    ),
]


_FACILITY_LABEL = {
    "open_field": "노지",
    "vinyl_house": "비닐하우스",
    "smart_farm": "스마트팜",
}


# ───────────────────────── 점수화 ─────────────────────────


def _climate_score(crop: CropProfile, clim: RegionClimate) -> float:
    """재배 적온 대비 기후 적합도. 적온이면 +20, 벗어나면 1℃당 -8."""
    t = clim.annual_temp
    if crop.temp_lo <= t <= crop.temp_hi:
        return 20.0
    dist = crop.temp_lo - t if t < crop.temp_lo else t - crop.temp_hi
    return -8.0 * dist


def _score(crop: CropProfile, clim: RegionClimate, input_: OnboardingInput) -> float:
    """작목 적합 점수(50 기준). 클수록 우선 추천."""
    s = 50.0 + _climate_score(crop, clim)

    if crop.crop_id in input_.preferredCrops:
        s += 10.0

    if input_.mode == "returning":
        s += crop.net_manwon / 120.0  # 수익성
        budget = input_.budgetManwon
        if budget is not None:
            s += 4.0 if budget >= crop.capital_manwon else -(crop.capital_manwon - budget) / 250.0
        if crop.needs_facility:
            s += 8.0 if input_.facility in ("vinyl_house", "smart_farm") else -14.0
        if crop.perennial and clim.frost_risk == "high":
            s -= 12.0
        s -= (crop.difficulty - 1) * 2.0
    else:
        # 주말농장: 소규모·단년·관리 용이 우선
        value = crop.yield_kg * crop.direct_price_won / 10_000.0
        s += value * 0.8
        s += (3 - crop.difficulty) * 3.0
        if crop.needs_facility:
            s -= 4.0

    return s


def _scale_factor(input_: OnboardingInput, base_pyeong: float) -> float:
    """면적을 평으로 환산해 기준 면적 대비 배율(0.2~5)을 구한다."""
    if input_.areaUnit == "sqm":
        pyeong = input_.area / 3.305785
    elif input_.areaUnit == "hectare":
        pyeong = input_.area * 3025.0
    else:
        pyeong = input_.area
    return max(0.2, min(5.0, pyeong / base_pyeong))


def _reason(crop: CropProfile, clim: RegionClimate, prov: str, input_: OnboardingInput) -> str:
    region = input_.region or prov
    if input_.mode == "returning":
        fac = _FACILITY_LABEL.get(input_.facility or "open_field", "노지")
        fit = (
            "재배 적온 범위에 잘 들어맞고"
            if crop.temp_lo <= clim.annual_temp <= crop.temp_hi
            else "기후가 적온과 다소 차이는 있지만 관리로 보완 가능하고"
        )
        return (
            f"{region}은 {clim.note} {crop.name} {fit}, {fac} 환경에서 "
            f"{crop.blurb} 작목입니다(연평균 {clim.annual_temp:.1f}℃ 기준)."
        )
    fit = (
        "생육 적온에 잘 맞아 실패율이 낮고"
        if crop.temp_lo <= clim.annual_temp <= crop.temp_hi
        else "기온 차이가 있어 시기 조절이 필요하지만"
    )
    return (
        f"{region}(연평균 {clim.annual_temp:.1f}℃)에서 {crop.name}은 {fit}, "
        f"{crop.blurb} 작목이라 주말 방문 관리에 적합합니다."
    )


_RANK_COLOR = ["red", "orange", "indigo"]


def _build_item(
    crop: CropProfile,
    clim: RegionClimate,
    prov: str,
    input_: OnboardingInput,
    score: float,
    rank: int,
) -> CropRecommendationItem:
    match_score = max(40, min(97, round(score)))
    calendar = [
        CalendarMonth(month=m, phase=_phase_at(crop.calendar, m))  # type: ignore[arg-type]
        for m in range(1, 13)
    ]
    if input_.mode == "returning":
        f = _scale_factor(input_, 300.0)
        revenue = round(crop.revenue_manwon * f)
        net = round(crop.net_manwon * f)
        yield_kg, price = 0, 0
    else:
        f = _scale_factor(input_, 100.0)
        revenue, net = 0, 0
        yield_kg, price = round(crop.yield_kg * f), crop.direct_price_won

    return CropRecommendationItem(
        cropId=crop.crop_id,
        name=crop.name,
        emoji=crop.emoji,
        matchScore=match_score,
        difficulty=crop.difficulty,
        expectedRevenueManwon=revenue,
        expectedNetManwon=net,
        expectedYieldKg=yield_kg,
        expectedDirectPriceWon=price,
        llmReason=_reason(crop, clim, prov, input_),
        tags=crop.tags,
        calendar=calendar,
        peerFarms=6 + match_score // 5,
        peerAgreeRate=max(45, min(92, match_score - 8)),
        color=_RANK_COLOR[rank],  # type: ignore[arg-type]
    )


def _phase_at(phases: dict[str, list[int]], month: int) -> str:
    phase = "rest"
    for p, months in phases.items():
        if month in months:
            phase = p
    return phase


def recommend(input_: OnboardingInput) -> RecommendationResponse:
    """주어진 사용자 입력에 대해 TOP-3 작목 추천.

    지역 기후 + 작목 요건을 점수화해 상위 3개를 반환한다. 주말농장 모드에서는
    소규모·단년 작목만(weekend_ok) 후보로 둔다.
    """
    prov, clim = _resolve_climate(input_)

    pool = [c for c in CATALOG if input_.mode == "returning" or c.weekend_ok]
    scored = sorted(
        ((c, _score(c, clim, input_)) for c in pool),
        key=lambda cs: (cs[1], cs[0].net_manwon, -CATALOG.index(cs[0])),
        reverse=True,
    )

    items = [
        _build_item(crop, clim, prov, input_, score, rank)
        for rank, (crop, score) in enumerate(scored[:3])
    ]
    return RecommendationResponse(mode=input_.mode, items=items)
