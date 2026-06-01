"""정부 지원사업 시드 로더.

seed/gov_support.json 은 큐레이션 데이터(귀농·청년농·농업 정책자금/지원).
조건은 공고에 따라 매년 바뀌므로 매칭 결과는 '참고'이고 최종은 공고 확인이 원칙.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "gov_support.json"


@lru_cache(maxsize=1)
def load_programs() -> list[dict[str, Any]]:
    with SEED_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)
