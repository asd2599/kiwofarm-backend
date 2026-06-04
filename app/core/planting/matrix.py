"""matrix.json + crops_master.json 로드·조회 (메모리 캐시).

설계: 백엔드 기동 시 별도 DB 없이 data/ 의 두 JSON 을 메모리에 로드한다.
  - crops_master.json : 작물 고정 메타(난이도·환경·일조·공간·생육일수·물).
  - matrix.json       : 월별 캘린더(파종/정식/관리/수확) + plain 설명 + 기후권 노트.
두 파일을 id 로 머지해 통합 Crop 레코드(dict)로 제공한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# 추천에 쓰이는 '심을 수 있는' 행동
PLANT_ACTIONS = ("파종", "정식")


@lru_cache(maxsize=1)
def _load() -> dict[str, dict[str, Any]]:
    """id → 통합 Crop 레코드. 최초 1회 로드 후 캐시."""
    master = json.loads((DATA_DIR / "crops_master.json").read_text(encoding="utf-8"))
    matrix = json.loads((DATA_DIR / "matrix.json").read_text(encoding="utf-8"))
    mcrops = matrix.get("crops", {})

    out: dict[str, dict[str, Any]] = {}
    for c in master["crops"]:
        cid = c["id"]
        mx = mcrops.get(cid, {})
        out[cid] = {
            "id": cid,
            "name": c["name"],
            "category": c["category"],
            "difficulty": c["difficulty"],
            "environments": c["environments"],
            "sunlight": c["sunlight"],
            "min_sun_hours": c["min_sun_hours"],
            "space": c["space"],
            "container_ok": c["container_ok"],
            "days_to_harvest": c["days_to_harvest"],
            "water_need": c["water_need"],
            "calendar": mx.get("calendar", {}),
            "climate_note": mx.get("climate_note", ""),
            "source": mx.get("source", c.get("source", "")),
            "needs_review": bool(mx.get("needs_review", False)),
        }
    return out


def matrix_version() -> str:
    matrix = json.loads((DATA_DIR / "matrix.json").read_text(encoding="utf-8"))
    return matrix.get("matrix_version", "")


def all_crops() -> list[dict[str, Any]]:
    return list(_load().values())


def get_crop(crop_id: str) -> dict[str, Any] | None:
    return _load().get(crop_id)


def actions_in_month(crop: dict[str, Any], month: int) -> list[dict[str, Any]]:
    return crop.get("calendar", {}).get(str(month), [])


def plantable_in_month(crop: dict[str, Any], month: int) -> bool:
    return any(a.get("action") in PLANT_ACTIONS for a in actions_in_month(crop, month))
