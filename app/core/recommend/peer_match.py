"""우수농가 유사매칭 (tier=premium 전용).

SmartFarmDATA2 시계열 API가 장애라도, 농가 로스터(seed/smartfarm_farms.json) 메타만으로
완숙토마토·딸기·파프리카에 대해 사용자 조건과 비슷한 실제 우수농가를 골라
정밀매칭 근거(지역·시설·면적·품종·작기)를 만든다.

매칭 점수(0~100):
  지역 45 (도 일치) 또는 기후(연평균기온) 근접도로 환산
  시설 30 (비닐/유리 ↔ 사용자 시설)
  면적 25 (평수 비율)
임계 MATCH_THRESHOLD 이상을 '유사농가'로 본다.

수익 컬럼이 원천에 없으므로 여기서도 수익은 다루지 않는다(엔진이 KAMIS와 결합).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.data.smartfarm import FarmRecord, load_farms

MATCH_THRESHOLD = 55.0
_DEFAULT_TEMP = 12.8  # 도 미상시 중부 기준 (engine.DEFAULT_CLIMATE 와 동일값)


def to_pyeong(area: float, unit: str) -> float:
    """면적을 평으로 환산."""
    if unit == "sqm":
        return area / 3.305785
    if unit == "hectare":
        return area * 3025.0
    return area


# 로스터 약식 도명 ↔ 온보딩 정식 도명 정규화 (양방향 비교용 canonical = 약식)
_PROVINCE_CANON = {
    "전북특별자치도": "전북", "전라북도": "전북",
    "전라남도": "전남", "경상남도": "경남", "경상북도": "경북",
    "강원특별자치도": "강원", "강원도": "강원",
    "충청북도": "충북", "충청남도": "충남", "경기도": "경기",
    "제주특별자치도": "제주", "제주도": "제주",
}
# canonical(약식) → PROVINCE_CLIMATE 키(정식)
_CANON_TO_FULL = {
    "전북": "전북특별자치도", "전남": "전라남도", "경남": "경상남도", "경북": "경상북도",
    "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도", "경기": "경기도",
    "제주": "제주특별자치도",
}


def canon_province(province: str | None) -> str:
    """도명을 약식 canonical 로 정규화 (광역시 등은 그대로)."""
    if not province:
        return ""
    return _PROVINCE_CANON.get(province, province)


def _province_temp(province: str) -> float:
    """광역시·도 연평균기온 조회. engine 의 PROVINCE_CLIMATE 를 지연 import(순환 방지)."""
    from app.core.recommend.engine import PROVINCE_CLIMATE

    canon = canon_province(province)
    key = _CANON_TO_FULL.get(canon, province)
    clim = PROVINCE_CLIMATE.get(key) or PROVINCE_CLIMATE.get(province)
    return clim.annual_temp if clim else _DEFAULT_TEMP


def _facility_score(user_facility: str | None, farm_facility: str) -> float:
    """사용자 시설 ↔ 농가 시설(비닐/유리) 적합도 0~30."""
    if user_facility == "vinyl_house":
        return 30.0 if farm_facility == "vinyl_house" else 18.0
    if user_facility == "smart_farm":
        return 30.0 if farm_facility == "glass_house" else 20.0
    # open_field 또는 미지정: 시설작목이라 약한 점수만
    return 10.0


def _similarity(
    farm: FarmRecord, user_canon: str, user_temp: float,
    area_pyeong: float, facility: str | None,
) -> float:
    s = 0.0
    if canon_province(farm.province) == user_canon:
        s += 45.0
    else:
        ft = _province_temp(farm.province)
        s += max(0.0, 35.0 - 7.0 * abs(user_temp - ft))

    s += _facility_score(facility, farm.facility)

    if area_pyeong > 0 and farm.area_pyeong > 0:
        ratio = min(area_pyeong, farm.area_pyeong) / max(area_pyeong, farm.area_pyeong)
        s += 25.0 * ratio
    return s


@dataclass(frozen=True)
class PeerStats:
    crop_id: str
    total: int  # 이 작목 우수농가 총수
    matched: int  # 유사조건 농가 수 (sim>=threshold)
    same_province: int  # 사용자와 같은 도의 농가 수
    user_province: str
    top_farms: list[FarmRecord]  # 유사도 상위 (최대 5)
    regions: list[tuple[str, int]]  # 매칭 농가 도별 분포 (상위)
    cultivars: list[tuple[str, int]]  # 매칭 농가 주요 품종 (상위)
    median_area_pyeong: int
    evidence: str  # 추천 이유/LLM 컨텍스트용 한 줄


def _median(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else round((s[n // 2 - 1] + s[n // 2]) / 2)


def match_peers(
    crop_id: str, user_province: str, area_pyeong: float, facility: str | None,
) -> PeerStats | None:
    """작목·사용자조건 유사 우수농가 통계. 로스터에 없으면 None."""
    farms = load_farms(crop_id=crop_id)
    if not farms:
        return None

    user_canon = canon_province(user_province)
    user_temp = _province_temp(user_province)
    scored = sorted(
        ((f, _similarity(f, user_canon, user_temp, area_pyeong, facility)) for f in farms),
        key=lambda fs: fs[1],
        reverse=True,
    )
    matched = [f for f, s in scored if s >= MATCH_THRESHOLD]
    # 임계 미달이라도 정밀매칭 근거는 줘야 하므로 최소 상위 3호는 유사농가로 인정
    if not matched:
        matched = [f for f, _ in scored[:3]]

    same_prov = sum(1 for f in farms if canon_province(f.province) == user_canon)
    regions = Counter(f.province for f in matched).most_common(4)
    cultivars = Counter(
        c.strip() for f in matched if f.cultivar for c in f.cultivar.split(",")
    ).most_common(4)
    median_area = _median([f.area_pyeong for f in matched])

    if same_prov:
        loc = f"{user_canon} 우수농가 {same_prov}호 포함"
    else:
        top_region = regions[0][0] if regions else "남부"
        loc = f"인근 {top_region} 우수농가 기준"
    cult = ", ".join(c for c, _ in cultivars[:3])
    evidence = (
        f"{loc}, 유사 재배조건 {len(matched)}호(주요 품종 {cult or '다양'}, "
        f"평균 {median_area:,}평) 실측 데이터 기반"
    )

    return PeerStats(
        crop_id=crop_id,
        total=len(farms),
        matched=len(matched),
        same_province=same_prov,
        user_province=user_province,
        top_farms=[f for f, _ in scored[:5]],
        regions=regions,
        cultivars=cultivars,
        median_area_pyeong=median_area,
        evidence=evidence,
    )
