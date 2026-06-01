"""국비지원 로컬푸드 직매장 운영현황 XLSX → 좌표 포함 JSON seed.

입력: docs/국비지원+로컬푸드+직매장+운영현황(`21년기준).xlsx (시트 '운영현황')
출력: backend/seed/localfood_stores.json

각 매장 주소를 Nominatim(OSM) 으로 1회 지오코딩해 lat/lng 를 박아둔다.
주소가 지번+도로명 혼합이라 실패하면 단계적으로 완화한 질의로 폴백한다.
런타임은 좌표 거리계산(haversine)만 하므로 외부호출 0.

카카오/브이월드 키가 생기면 geocode() 만 교체해 재실행하면 된다.

재실행:
    uv run python scripts/build_localfood_seed.py
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import openpyxl

REPO = Path(__file__).resolve().parents[2]
XLSX = next(
    p for p in (REPO / "docs").glob("*.xlsx") if "직매장" in p.name or "로컬" in p.name
)
OUT = REPO / "backend" / "seed" / "localfood_stores.json"

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "kiwofarm-localfood-seed/1.0 (codelab2005@gmail.com)"}

# 시도 약칭 → Nominatim 이 잘 무는 정식 표기
SIDO_FULL = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시",
    "인천": "인천광역시", "광주": "광주광역시", "대전": "대전광역시",
    "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도",
    "강원": "강원도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전라북도", "전남": "전라남도", "경북": "경상북도",
    "경남": "경상남도", "제주": "제주특별자치도",
}

_ROAD = re.compile(r"([가-힣A-Za-z0-9]+(?:로|길))\s*([\d\-]+)")


def _clean(addr: str) -> str:
    return re.sub(r"\s+", " ", (addr or "").strip())


def _queries(sido: str, sigungu: str, address: str) -> list[str]:
    """완화 순서대로 시도할 질의 목록."""
    addr = _clean(address)
    full_sido = SIDO_FULL.get(sido, sido)
    qs: list[str] = []
    if addr:
        qs.append(addr)
    # 도로명+번지만 시도/시군구와 붙여 재질의 (지번/리 노이즈 제거)
    m = _ROAD.search(addr)
    if m:
        qs.append(f"{full_sido} {sigungu} {m.group(1)} {m.group(2)}")
    # 시군구 중심 폴백
    qs.append(f"{full_sido} {sigungu}")
    qs.append(f"{full_sido} {sigungu}군")
    # 중복 제거(순서 유지)
    seen: set[str] = set()
    return [q for q in qs if q and not (q in seen or seen.add(q))]


def geocode(sido: str, sigungu: str, address: str) -> tuple[float | None, float | None, str]:
    """(lat, lng, source). source 는 'address' | 'road' | 'sigungu' | 'none'."""
    tags = ["address", "road", "sigungu", "sigungu"]
    for q, tag in zip(_queries(sido, sigungu, address), tags):
        try:
            r = httpx.get(
                NOMINATIM,
                params={"q": q, "format": "json", "limit": 1, "countrycodes": "kr"},
                headers=UA,
                timeout=15,
            )
            r.raise_for_status()
            j = r.json()
        except Exception:
            j = None
        time.sleep(1.1)  # Nominatim 이용약관: 최대 1req/s
        if j:
            return float(j[0]["lat"]), float(j[0]["lon"]), tag
    return None, None, "none"


def build() -> list[dict]:
    wb = openpyxl.load_workbook(XLSX, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() for h in rows[0]]
    idx = {name: header.index(name) for name in header}

    out: list[dict] = []
    for r in rows[1:]:
        if r[idx["번호"]] is None:
            continue
        sido = _clean(str(r[idx["시도"]] or ""))
        sigungu = _clean(str(r[idx["시군구"]] or ""))
        address = _clean(str(r[idx["주소"]] or ""))
        lat, lng, source = geocode(sido, sigungu, address)
        store = {
            "id": int(r[idx["번호"]]),
            "sido": sido,
            "sigungu": sigungu,
            "name": _clean(str(r[idx["매장명"]] or "")),
            "operator": _clean(str(r[idx["운영주체"]] or "")),
            "address": address,
            "phone": _clean(str(r[idx["연락처"]] or "")),
            "opened": _clean(str(r[idx["개장일"]] or "")),
            "lat": lat,
            "lng": lng,
            "geocode_source": source,
        }
        out.append(store)
        print(f"{store['id']:>3} {source:>8}  {store['name'][:24]}")
    return out


def main() -> None:
    stores = build()
    OUT.write_text(json.dumps(stores, ensure_ascii=False, indent=2), encoding="utf-8")
    by_src: dict[str, int] = {}
    for s in stores:
        by_src[s["geocode_source"]] = by_src.get(s["geocode_source"], 0) + 1
    print(f"\n총 {len(stores)}건 → {OUT}")
    print("지오코딩 소스별:", by_src)


if __name__ == "__main__":
    main()
