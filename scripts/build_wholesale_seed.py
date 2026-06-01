"""2023년 도매시장 규모 현황 CSV → 좌표 포함 JSON seed.

입력: docs/2023년 도매시장 규모 현황.csv (cp949, 49개 시장)
출력: backend/seed/wholesale_markets.json

CSV 에는 주소·좌표가 없다. 시장별 소재 시·구(자치구) 를 아래 LOCALITY 표로
큐레이션해 Nominatim 으로 지오코딩한다. 지명(랜드마크→자치구→시) 순으로 시도하며
대부분 자치구 중심 좌표를 얻는다(같은 도시 내 시장도 구분, 거리추천엔 충분).

카카오/브이월드 키가 생기면 시장명 직접 지오코딩으로 정밀도를 올릴 수 있다.
LOCALITY 의 query 를 수정하고 재실행하면 된다.

재실행:
    uv run python scripts/build_wholesale_seed.py
"""

from __future__ import annotations

import csv
import glob
import json
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
CSV_PATH = glob.glob(str(REPO / "docs" / "*도매시장*.csv"))[0]
OUT = REPO / "backend" / "seed" / "wholesale_markets.json"

NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "kiwofarm-wholesale-seed/1.0 (codelab2005@gmail.com)"}

# CSV 순서(1~49)와 1:1. (시도, [지오코딩 질의 후보 — 랜드마크/자치구→시 순]).
# 자치구가 확실치 않은 곳은 시 단위로만 둔다.
LOCALITY: list[tuple[str, list[str]]] = [
    ("서울", ["서울 송파구 가락동 가락시장", "서울특별시 송파구 가락동", "서울특별시 송파구"]),
    ("서울", ["서울 강서구 외발산동 농산물도매시장", "서울특별시 강서구 외발산동", "서울특별시 강서구"]),
    ("부산", ["부산 사상구 엄궁동 농산물도매시장", "부산광역시 사상구 엄궁동", "부산광역시 사상구"]),
    ("부산", ["부산 해운대구 반여동 농산물도매시장", "부산광역시 해운대구 반여동", "부산광역시 해운대구"]),
    ("부산", ["부산 서구 암남동 국제수산물도매시장", "부산광역시 서구 암남동", "부산광역시 서구"]),
    ("대구", ["대구 북구 매천동 농수산물도매시장", "대구광역시 북구 매천동", "대구광역시 북구"]),
    ("인천", ["인천 남동구 남촌동 농산물도매시장", "인천광역시 남동구 남촌동", "인천광역시 남동구"]),
    ("인천", ["인천 부평구 삼산동 농산물도매시장", "인천광역시 부평구 삼산동", "인천광역시 부평구"]),
    ("광주", ["광주 북구 각화동 농산물도매시장", "광주광역시 북구 각화동", "광주광역시 북구"]),
    ("광주", ["광주 서구 매월동 농수산물도매시장", "광주광역시 서구 매월동", "광주광역시 서구"]),
    ("대전", ["대전 대덕구 오정동 농수산물도매시장", "대전광역시 대덕구 오정동", "대전광역시 대덕구"]),
    ("대전", ["대전 유성구 노은동 농수산물도매시장", "대전광역시 유성구 노은동", "대전광역시 유성구"]),
    ("울산", ["울산 남구 삼산동 농수산물도매시장", "울산광역시 남구 삼산동", "울산광역시 남구"]),
    ("경기", ["수원 권선구 농수산물도매시장", "경기도 수원시 권선구", "경기도 수원시"]),
    ("경기", ["안양 동안구 호계동 농수산물도매시장", "경기도 안양시 동안구 호계동", "경기도 안양시"]),
    ("경기", ["안산 단원구 성곡동 농수산물도매시장", "경기도 안산시 단원구 성곡동", "경기도 안산시"]),
    ("경기", ["구리 인창동 농수산물도매시장", "경기도 구리시 인창동", "경기도 구리시"]),
    ("강원", ["춘천 동내면 농수산물도매시장", "강원특별자치도 춘천시 동내면", "강원특별자치도 춘천시"]),
    ("강원", ["원주 우산동 농산물도매시장", "강원특별자치도 원주시 우산동", "강원특별자치도 원주시"]),
    ("강원", ["강릉 농산물도매시장", "강원특별자치도 강릉시 입암동", "강원특별자치도 강릉시"]),
    ("충북", ["청주 흥덕구 봉명동 농수산물도매시장", "충청북도 청주시 흥덕구", "충청북도 청주시"]),
    ("충북", ["충주 농수산물도매시장", "충청북도 충주시"]),
    ("충남", ["천안 서북구 신당동 농수산물도매시장", "충청남도 천안시 서북구", "충청남도 천안시"]),
    ("전북", ["전주 덕진구 팔복동 농수산물도매시장", "전북특별자치도 전주시 덕진구", "전북특별자치도 전주시"]),
    ("전북", ["익산 농수산물도매시장", "전북특별자치도 익산시"]),
    ("전북", ["정읍 농산물도매시장", "전북특별자치도 정읍시"]),
    ("전남", ["순천 농산물도매시장", "전라남도 순천시"]),
    ("경북", ["포항 북구 농산물도매시장", "경상북도 포항시 북구", "경상북도 포항시"]),
    ("경북", ["안동 농수산물도매시장", "경상북도 안동시"]),
    ("경북", ["구미 농산물도매시장", "경상북도 구미시"]),
    ("경남", ["창원 의창구 팔용동 농산물도매시장", "경상남도 창원시 의창구 팔용동", "경상남도 창원시"]),
    ("경남", ["창원 마산회원구 내서읍 농산물도매시장", "경상남도 창원시 마산회원구 내서읍", "경상남도 창원시"]),
    ("경남", ["진주 농산물도매시장", "경상남도 진주시"]),
    ("서울", ["서울 동작구 노량진수산시장", "서울특별시 동작구 노량진동", "서울특별시 동작구"]),
    ("서울", ["서울 서초구 양재동 양곡도매시장", "서울특별시 서초구 양재동", "서울특별시 서초구"]),
    ("대구", ["대구 북구 검단동 축산물도매시장", "대구광역시 북구 검단동", "대구광역시 북구"]),
    ("대구", ["대구 중구 남성로 약령시", "대구광역시 중구 남성로", "대구광역시 중구"]),
    ("인천", ["인천 서구 가좌동 축산물도매시장", "인천광역시 서구 가좌동", "인천광역시 서구"]),
    ("광주", ["광주 북구 각화동 축산물도매시장", "광주광역시 북구", "광주광역시"]),
    ("전남", ["목포 농산물도매시장", "전라남도 목포시"]),
    ("전남", ["여수 농산물도매시장", "전라남도 여수시"]),
    ("경북", ["포항 남구 수산물도매시장", "경상북도 포항시 남구", "경상북도 포항시"]),
    ("경북", ["경주 농산물도매시장", "경상북도 경주시"]),
    ("경북", ["김천 농산물도매시장", "경상북도 김천시"]),
    ("경북", ["영천 농산물도매시장", "경상북도 영천시"]),
    ("경북", ["영천 약초도매시장", "경상북도 영천시"]),
    ("경기", ["안양 축산물도매시장", "경기도 안양시"]),
    ("경북", ["상주 농산물도매시장", "경상북도 상주시"]),
    ("경북", ["영주 농산물도매시장", "경상북도 영주시"]),
]

_TAGS = ["landmark", "district", "city", "city"]


def geocode(queries: list[str]) -> tuple[float | None, float | None, str]:
    for q, tag in zip(queries, _TAGS):
        try:
            r = httpx.get(
                NOMINATIM,
                params={"q": q, "format": "json", "limit": 1, "countrycodes": "kr"},
                headers=UA,
                timeout=15,
            )
            j = r.json()
        except Exception:
            j = None
        time.sleep(1.1)
        if j:
            return float(j[0]["lat"]), float(j[0]["lon"]), tag
    return None, None, "none"


def _to_int(v: str) -> int | None:
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def build() -> list[dict]:
    with open(CSV_PATH, encoding="cp949", newline="") as f:
        rows = list(csv.reader(f))
    data = rows[1:]
    if len(data) != len(LOCALITY):
        raise SystemExit(f"행 수 불일치: CSV {len(data)} vs LOCALITY {len(LOCALITY)}")

    out: list[dict] = []
    for i, (row, (sido, queries)) in enumerate(zip(data, LOCALITY), 1):
        lat, lng, source = geocode(queries)
        out.append({
            "id": i,
            "sido": sido,
            "name": row[2].strip(),
            "category": row[1].strip(),       # 공영/일반법정/민영
            "opened": row[3].strip(),          # YYYYMM
            "corp_count": _to_int(row[4]),     # 도매법인 수
            "merchant_count": _to_int(row[5]),  # 중도매인 수
            "land_area_sqm": _to_int(row[6]),  # 부지면적(㎡)
            "geocode_query": queries[0],
            "lat": lat,
            "lng": lng,
            "geocode_source": source,
        })
        print(f"{i:>2} {source:>9}  {row[2].strip()}")
    return out


def main() -> None:
    markets = build()
    OUT.write_text(json.dumps(markets, ensure_ascii=False, indent=2), encoding="utf-8")
    by_src: dict[str, int] = {}
    for m in markets:
        by_src[m["geocode_source"]] = by_src.get(m["geocode_source"], 0) + 1
    print(f"\n총 {len(markets)}건 → {OUT}")
    print("지오코딩 소스별:", by_src)


if __name__ == "__main__":
    main()
