"""기상청 ASOS (지상관측) 클라이언트.

사용처:
  - 작목 추천: 지역별 기후 매칭 (평균기온·일조·강수)
  - 디지털 트윈 위기 알림: 평년 대비 이상기후 시점 감지

엔드포인트:
  - https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php (일자료)
  - 인증키: settings.kma_api_key

TODO 실구현:
  - httpx.AsyncClient 로 비동기 호출
  - 응답 파싱: 콘솔 텍스트 포맷이라 줄바꿈 split 필요
  - 응답 캐시 (region + period 키, 24h TTL)
"""

from dataclasses import dataclass
from datetime import date

from app.config import settings


@dataclass(frozen=True)
class DailyWeather:
    obs_date: date
    region: str
    avg_temp_c: float
    sunshine_hours: float
    precipitation_mm: float
    humidity_pct: float


async def fetch_daily(region: str, start: date, end: date) -> list[DailyWeather]:
    """지역 일자료 조회.

    현재 구현: 빈 리스트. KMA API 연결 전 placeholder.
    """
    _ = (settings.kma_api_key, region, start, end)
    return []
