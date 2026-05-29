"""recentlyPriceTrendList 용 productno ↔ 품목 매핑 시드 생성.

recentlyPriceTrendList 는 item/kind 코드 대신 KAMIS 내부 상품번호(p_productno)만
받는다. 번호↔품목 공식표가 없어, 일별 도매목록(dailyPriceByCategoryList)의
최근 3개 시점 가격과 각 productno 의 (d0,d10,d20) 을 대조해 자동 매핑한다.

출력: seed/kamis_productno.json
실행: uv run python scripts/build_kamis_productno.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.data import kamis

CATEGORIES = ["100", "200", "300", "400", "500", "600"]
PRODUCTNO_MAX = 250
OUT_PATH = Path(__file__).resolve().parents[1] / "seed" / "kamis_productno.json"


def _pi(raw) -> int | None:
    return kamis._parse_price(kamis._parse_str(raw))


async def build_daily_index(client: httpx.AsyncClient, regday: date) -> dict:
    """(dpr1,dpr3,dpr4) → 품목정보. 도매(02) 전 부류."""
    index: dict[tuple[int, int, int], dict] = {}
    for cat in CATEGORIES:
        items = await kamis._fetch_daily_category("02", cat, regday)
        for it in items:
            d1, d3, d4 = _pi(it.get("dpr1")), _pi(it.get("dpr3")), _pi(it.get("dpr4"))
            if d1 is None:
                continue
            key = (d1, d3 if d3 is not None else -1, d4 if d4 is not None else -1)
            index.setdefault(key, {
                "category_code": cat,
                "item_code": kamis._parse_str(it.get("item_code")),
                "item_name": kamis._parse_str(it.get("item_name")),
                "kind_code": kamis._parse_str(it.get("kind_code")),
                "kind_name": kamis._parse_str(it.get("kind_name")),
                "rank": kamis._parse_str(it.get("rank")),
                "rank_code": kamis._parse_str(it.get("rank_code")),
                "unit": kamis._parse_str(it.get("unit")),
            })
    return index


async def trend_points(client: httpx.AsyncClient, pno: int) -> tuple[int, int, int] | None:
    params = {
        "action": "recentlyPriceTrendList",
        "p_productno": str(pno),
        "p_productclscode": "02",
        "p_cert_key": settings.kamis_cert_key,
        "p_cert_id": settings.kamis_cert_id,
        "p_returntype": "json",
    }
    r = await client.get(kamis.KAMIS_URL, params=params)
    body = r.json()
    price = body.get("price")
    if not isinstance(price, list):
        return None
    cur = next((p for p in price if p.get("yyyy") == "2026"), None)
    if not cur:
        return None
    d0, d10, d20 = _pi(cur.get("d0")), _pi(cur.get("d10")), _pi(cur.get("d20"))
    if d0 is None:
        return None
    return (d0, d10 if d10 is not None else -1, d20 if d20 is not None else -1)


async def main():
    regday = date.today()
    async with httpx.AsyncClient(timeout=20.0, verify=kamis._SSL_CTX) as client:
        # 오늘 데이터 없으면 며칠 뒤로
        index = {}
        for back in range(4):
            index = await build_daily_index(client, regday - timedelta(days=back))
            if index:
                regday = regday - timedelta(days=back)
                break
        print(f"daily index: {len(index)} keys (regday={regday})")

        sem = asyncio.Semaphore(8)

        async def one(pno):
            async with sem:
                try:
                    return pno, await trend_points(client, pno)
                except Exception:
                    return pno, None

        results = await asyncio.gather(*[one(p) for p in range(1, PRODUCTNO_MAX + 1)])

    mapping = []
    unmatched = []
    for pno, pts in results:
        if not pts:
            continue
        d0, d10, d20 = pts
        hit = index.get((d0, d10, d20))
        if hit:
            mapping.append({"productno": pno, **hit})
        else:
            # d0 만이라도 단일 매칭되면 약한 매칭
            cands = [v for k, v in index.items() if k[0] == d0]
            if len(cands) == 1:
                mapping.append({"productno": pno, "weak": True, **cands[0]})
            else:
                unmatched.append((pno, d0, len(cands)))

    OUT_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"matched {len(mapping)} productno -> wrote {OUT_PATH.name}")
    print(f"unmatched productno: {len(unmatched)} (sample: {unmatched[:10]})")
    # 주요 작물 확인
    by_item = {}
    for m in mapping:
        by_item.setdefault(m["item_code"], []).append((m["productno"], m["rank_code"], m.get("weak", False)))
    for ic in ["225", "422", "211", "152", "411", "223", "241", "245"]:
        print(f"  item {ic}: {by_item.get(ic)}")


asyncio.run(main())
