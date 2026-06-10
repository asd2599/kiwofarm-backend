"""서비스 기준 시각 = 한국 표준시(KST).

fly 컨테이너는 UTC로 동작해 `datetime.now()`/`date.today()`가 UTC 기준이 된다.
수확일·streak 경계·EXIF 비교는 사용자 체감(KST)과 어긋나면 하루씩 밀리므로,
시간이 필요한 도메인 로직은 여기 헬퍼를 쓴다.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def kst_now() -> datetime:
    """KST 기준 naive datetime. EXIF(카메라 로컬시각, naive)와 직접 비교용."""
    return datetime.now(KST).replace(tzinfo=None)


def kst_today() -> date:
    """KST 기준 오늘 날짜."""
    return datetime.now(KST).date()
