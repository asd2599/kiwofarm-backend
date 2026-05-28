"""SmartFarmKorea (농촌진흥청 스마트팜코리아) CSV 로더.

사용처:
  - 작목 추천 학습 (XGBoost): 우수농가 5호 + 혁신밸리 107호 = 112호 영농일지
  - 디지털 트윈 베이스라인: 작목·면적·지역별 월별 수확량/노동/비용

TODO 실구현:
  - CSV 위치 결정 (`backend/data/smartfarm/*.csv`)
  - pandas.read_csv 로 로드 후 cache
  - 작목·지역·시설 필터 함수
"""

from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "smartfarm"


@dataclass(frozen=True)
class FarmRecord:
    """우수농가 한 호의 1년 영농일지 요약."""

    farm_id: str
    region: str
    crop_id: str
    facility: str  # open_field | vinyl_house | smart_farm
    area_pyeong: int
    revenue_manwon: int
    cost_manwon: int
    labor_hours: int


def load_farms(crop_id: str | None = None, region: str | None = None) -> list[FarmRecord]:
    """우수농가 영농일지 로드.

    현재 구현: 빈 리스트. CSV 연결 전 placeholder.
    """
    _ = DATA_DIR  # silence unused-import
    _ = (crop_id, region)
    return []
