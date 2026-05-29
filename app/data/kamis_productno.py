"""recentlyPriceTrendList 용 productno ↔ 품목 매핑 (메모리 캐시).

seed/kamis_productno.json 은 scripts/build_kamis_productno.py 가 KAMIS 일별
도매목록 가격 대조로 생성한다. 재생성 외에는 읽기 전용.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "kamis_productno.json"


class ProductnoRecord(TypedDict, total=False):
    productno: int
    category_code: str
    item_code: str
    item_name: str
    kind_code: str
    kind_name: str
    rank: str
    rank_code: str
    unit: str
    weak: bool


@lru_cache(maxsize=1)
def _load() -> list[ProductnoRecord]:
    with SEED_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _by_item() -> dict[str, list[ProductnoRecord]]:
    out: dict[str, list[ProductnoRecord]] = {}
    for r in _load():
        out.setdefault(r["item_code"], []).append(r)
    return out


def find(item_code: str, kind_code: str = "") -> ProductnoRecord | None:
    """item_code 의 도매 productno 1건. 상품(04) 우선, 품종 일치 우선."""
    rows = _by_item().get(item_code)
    if not rows:
        return None

    def score(r: ProductnoRecord) -> tuple[int, int]:
        return (
            0 if r.get("rank_code") == "04" else 1,
            0 if kind_code and r.get("kind_code") == kind_code else 1,
        )

    return sorted(rows, key=score)[0]
