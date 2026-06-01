"""도매시장 좌표 데이터 + 거리 추천.

seed/wholesale_markets.json 은 scripts/build_wholesale_seed.py 가 생성한다.
좌표가 시드에 박혀 있어 거리 추천은 외부호출 없이 haversine 만 쓴다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import TypedDict

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "wholesale_markets.json"


class Market(TypedDict, total=False):
    id: int
    sido: str
    name: str
    category: str
    opened: str
    corp_count: int | None
    merchant_count: int | None
    land_area_sqm: int | None
    lat: float | None
    lng: float | None
    geocode_source: str


@lru_cache(maxsize=1)
def load_markets() -> list[Market]:
    with SEED_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * asin(sqrt(a))


def nearest_markets(lat: float, lng: float, limit: int = 5) -> list[dict]:
    """좌표 기준 가까운 도매시장 limit 건 (거리km 포함, 가까운 순)."""
    scored: list[tuple[float, Market]] = []
    for m in load_markets():
        mlat, mlng = m.get("lat"), m.get("lng")
        if mlat is None or mlng is None:
            continue
        scored.append((_haversine_km(lat, lng, mlat, mlng), m))
    scored.sort(key=lambda x: x[0])
    return [{**m, "distance_km": round(dist, 1)} for dist, m in scored[:limit]]
