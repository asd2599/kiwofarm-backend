"""v3 작물 식별자 표준(crops_master 슬러그) + 레거시 KAMIS 코드 매핑.

v3 의 작물 마스터는 data/crops_master.json (40종, core/planting/matrix.py 가
로드) 이고 식별자는 영문 슬러그다(예: lettuce). 이 모듈은 그 표준과
레거시 데이터(KAMIS itemCode 키 임베딩·farm_plan DB)를 잇는 다리:

  - KAMIS_TO_SLUG: 기존 임베딩/DB 의 KAMIS itemCode → 슬러그 (40종 해당분만)
  - slug_for(): RAG 스토어 키 정규화에 사용 (ingest.crop_key)
  - find_by_name(): 한글 작물명 → 마스터 레코드 (monthFd 식재료 매칭 등)

40종 밖 KAMIS 코드는 매핑이 없으며(None), v3 서비스 대상이 아니다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.core.planting import matrix

# 레거시 KAMIS itemCode(또는 x_* 합성키) → crops_master 슬러그.
# scripts/migrate_data_v3.py 의 임베딩 리네이밍과 ingest.crop_key 정규화가 공유한다.
KAMIS_TO_SLUG: dict[str, str] = {
    "151": "sweet_potato",  # 고구마
    "152": "potato",  # 감자
    "213": "spinach",  # 시금치
    "214": "lettuce",  # 상추
    "216": "mustard_greens",  # 갓
    "223": "cucumber",  # 오이
    "224": "pumpkin",  # 호박
    "225": "tomato",  # 토마토
    "226": "strawberry",  # 딸기
    "231": "radish",  # 무
    "232": "carrot",  # 당근
    "242": "chili_pepper",  # 풋고추 → 고추
    "244": "garlic",  # 피마늘 → 마늘
    "245": "onion",  # 양파
    "246": "green_onion",  # 파 → 대파
    "251": "eggplant",  # 가지
    "253": "perilla",  # 깻잎
    "254": "garlic_chives",  # 부추
    "256": "paprika",  # 파프리카
    "263": "bokchoy",  # 청경채
    "264": "kale",  # 케일
    "276": "spring_onion",  # 쪽파
    "422": "cherry_tomato",  # 방울토마토
    "x_corn": "corn",  # 옥수수 (구 합성키)
}

# 한글명 매칭 별칭: 외부 데이터 표기 → 마스터 작물명.
NAME_ALIAS: dict[str, str] = {
    "총각무": "알타리무",
    "풋고추": "고추",
    "홍고추": "고추",
    "피마늘": "마늘",
    "파": "대파",
    "들깻잎": "깻잎",
}


def slug_for(item_code: str) -> str | None:
    """레거시 KAMIS itemCode → 슬러그. 40종 밖이면 None."""
    return KAMIS_TO_SLUG.get(item_code)


def is_slug(key: str) -> bool:
    """키가 이미 v3 슬러그인지 (마스터에 존재하는지)."""
    return matrix.get_crop(key) is not None


@lru_cache(maxsize=1)
def _name_index() -> dict[str, dict[str, Any]]:
    idx = {c["name"]: c for c in matrix.all_crops()}
    for alias, name in NAME_ALIAS.items():
        if name in idx:
            idx.setdefault(alias, idx[name])
    return idx


def find_by_name(name: str) -> dict[str, Any] | None:
    """한글 작물명(별칭 포함) → 마스터 레코드. 공백 제거 정확 일치만."""
    return _name_index().get(name.replace(" ", "").strip())


__all__ = ["KAMIS_TO_SLUG", "NAME_ALIAS", "slug_for", "is_slug", "find_by_name"]
