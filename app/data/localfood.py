"""로컬푸드 직매장 좌표 데이터 + 거리 추천 + 런타임 주소 지오코딩.

seed/localfood_stores.json 은 scripts/build_localfood_seed.py 가 생성한다.
좌표는 시드에 박혀 있으므로 거리 추천은 외부호출 없이 haversine 만 쓴다.
사용자가 입력한 판매 위치 주소만 런타임에 1회 지오코딩한다(Nominatim, 캐시).
"""

from __future__ import annotations

import json
from functools import lru_cache
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import TypedDict

import httpx

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "localfood_stores.json"

NOMINATIM = "https://nominatim.openstreetmap.org/search"
_UA = {"User-Agent": "kiwofarm-localfood/1.0 (codelab2005@gmail.com)"}


class Store(TypedDict, total=False):
    id: int
    sido: str
    sigungu: str
    name: str
    operator: str
    address: str
    phone: str
    opened: str
    lat: float | None
    lng: float | None
    geocode_source: str


@lru_cache(maxsize=1)
def load_stores() -> list[Store]:
    with SEED_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * asin(sqrt(a))


def nearest_stores(lat: float, lng: float, limit: int = 5) -> list[dict]:
    """좌표 기준 가까운 직매장 limit 건 (거리km 포함, 가까운 순)."""
    scored: list[tuple[float, Store]] = []
    for s in load_stores():
        slat, slng = s.get("lat"), s.get("lng")
        if slat is None or slng is None:
            continue
        scored.append((_haversine_km(lat, lng, slat, slng), s))
    scored.sort(key=lambda x: x[0])
    out: list[dict] = []
    for dist, s in scored[:limit]:
        out.append({**s, "distance_km": round(dist, 1)})
    return out


_geocode_cache: dict[str, tuple[float, float, str] | None] = {}


async def geocode_address(query: str) -> tuple[float, float, str] | None:
    """판매 위치 주소 → (lat, lng, display). 실패 시 None. 메모리 캐시."""
    q = " ".join((query or "").split())
    if not q:
        return None
    if q in _geocode_cache:
        return _geocode_cache[q]
    result: tuple[float, float, str] | None = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                NOMINATIM,
                params={"q": q, "format": "json", "limit": 1, "countrycodes": "kr"},
                headers=_UA,
            )
            resp.raise_for_status()
            j = resp.json()
        if j:
            result = (float(j[0]["lat"]), float(j[0]["lon"]), j[0].get("display_name", q))
    except Exception:
        result = None
    _geocode_cache[q] = result
    return result
