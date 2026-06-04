"""커뮤니티 비교 통계 시드 로더 (메모리 캐시).

seed/community_stats.json 은 scripts/build_community_stats.py 가 생성한
합성 분포(실사용자 풀 확보 전). 실서비스 전환 시 이 모듈만 실제 집계로 교체한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "community_stats.json"


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    with SEED_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def overall() -> dict[str, Any]:
    """전체 커뮤니티 통계 (growers, weeklyRecords 분위수)."""
    return _load()["all"]


def for_crop(slug: str) -> dict[str, Any] | None:
    for c in _load()["crops"]:
        if c["slug"] == slug:
            return c
    return None
