"""농사로 (농촌진흥청) 작목·병해충 클라이언트.

사용처:
  - 작목 추천: 작목별 재배 적지/기후 조건
  - 디지털 트윈 위기 알림: 병해충 발생 시점·방제 정보
  - 영농 캘린더: 작목별 표준 생육 단계

엔드포인트:
  - http://api.nongsaro.go.kr/service/cropInfo
  - 인증키: settings.nongsaro_api_key (api.nongsaro.go.kr 자체 발급, data.go.kr 통합키와 별개)

TODO 실구현:
  - 작목 마스터 동기화 (cropId ↔ 농사로 cntntsNo)
  - 병해충 캘린더 동기화 (월별 발생 위험도)
  - SQLite 또는 PostgreSQL 캐시
"""

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class PestRisk:
    crop_id: str
    pest_name: str
    month: int  # 1-12
    severity: str  # low | medium | high
    control_note: str


async def fetch_pest_risks(crop_id: str) -> list[PestRisk]:
    """작목 병해충 발생 캘린더.

    현재 구현: 빈 리스트. 농사로 API 연결 전 placeholder.
    """
    _ = (settings.nongsaro_api_key, crop_id)
    return []
