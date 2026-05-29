"""KAMIS 부류·품목·품종 코드 마스터 (메모리 캐시).

seed/kamis_crops.json 은 scripts/build_kamis_seed.py 가 docs 의
'농축수산물 품목 및 등급 코드표.xlsx'에서 생성한다. 재생성 외에는 읽기 전용.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

SEED_PATH = Path(__file__).resolve().parents[2] / "seed" / "kamis_crops.json"


class CropRecord(TypedDict):
    groupCode: str
    groupName: str
    itemCode: str
    itemName: str
    kindCode: str
    kindName: str
    label: str
    searchText: str


@lru_cache(maxsize=1)
def _load() -> list[CropRecord]:
    with SEED_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def all_crops() -> list[CropRecord]:
    return _load()


def get_by_codes(item_code: str, kind_code: str) -> CropRecord | None:
    """itemCode·kindCode 조합으로 단일 작목 조회."""
    for row in _load():
        if row["itemCode"] == item_code and row["kindCode"] == kind_code:
            return row
    return None


def search(query: str, limit: int = 10) -> list[CropRecord]:
    """품목명·품종명·부류명 부분일치. 매칭 강도가 높은 순으로 정렬.

    랭킹:
      0 정확 일치 (itemName 또는 kindName)
      1 itemName/kindName 접두 일치
      2 부분 일치
    """
    q = query.strip().lower()
    if not q:
        return []

    scored: list[tuple[int, int, CropRecord]] = []
    for i, row in enumerate(_load()):
        item = row["itemName"].lower()
        kind = row["kindName"].lower()
        text = row["searchText"].lower()

        if q == item or q == kind:
            rank = 0
        elif item.startswith(q) or kind.startswith(q):
            rank = 1
        elif q in text:
            rank = 2
        else:
            continue
        scored.append((rank, i, row))

    scored.sort(key=lambda t: (t[0], t[1]))
    return [row for _, _, row in scored[:limit]]
