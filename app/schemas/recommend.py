"""면적 단위 타입 — farmplan 스키마가 공유한다.

(옛 추천 API 스키마는 v3 피벗으로 제거됨. 살아있는 추천은 core/planting/recommend.py.)
"""

from typing import Literal

AreaUnit = Literal["pyeong", "sqm", "hectare"]
