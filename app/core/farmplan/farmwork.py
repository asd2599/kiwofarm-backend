"""농사로 공식 농작업일정(raw_farmwork.json) 주입 — 캘린더 생성의 권위 있는 시기 근거.

cultivation 임베딩이 GPT 생성(source=general)이라 시기가 부정확했다. 정작 정확한
파종/정식/수확 월 데이터(농사로 farmWorkingPlanNew)는 raw_farmwork.json 에 있으나
생성에 안 쓰이고 있었다. slug→cntntsNo(crops_master)→주요농작업(infoSeCode 410001)
행을 프롬프트에 직접 넣어 GPT 가 이 시기를 따르게 한다.
"""

from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path

# raw_farmwork.json·crops_master.json 은 backend/data/ (app/data/ 아님).
# app/core/farmplan/farmwork.py → parents[3] = backend.
_DATA = Path(__file__).resolve().parents[3] / "data"
_MAIN_WORK = "410001"  # 농작업일정(주요농작업) — 파종·정식·수확 등 핵심 작업


@lru_cache(maxsize=1)
def _farmwork() -> dict:
    with open(_DATA / "raw_farmwork.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def _slug_to_cntnts() -> dict[str, list[str]]:
    with open(_DATA / "crops_master.json", encoding="utf-8") as f:
        data = json.load(f)
    # {"crops": [...]} 래퍼 또는 리스트 둘 다 허용.
    crops = data.get("crops", []) if isinstance(data, dict) else data
    out: dict[str, list[str]] = {}
    for c in crops:
        if not isinstance(c, dict):
            continue
        slug = c.get("id")
        nos = c.get("nongsaro_cntnts_no") or []
        if slug and nos:
            out[slug] = [str(n) for n in nos]
    return out


def schedule_for(slug: str) -> str:
    """slug 의 농사로 주요 농작업일정을 프롬프트용 텍스트로. 없으면 ''."""
    cntnts = _slug_to_cntnts().get(slug)
    if not cntnts:
        return ""
    fw = _farmwork()
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for no in cntnts:
        entry = fw.get(no)
        if not entry:
            continue
        for r in entry.get("rows", []):
            if r.get("infoSeCode") != _MAIN_WORK:
                continue
            op = (r.get("opertNm") or "").strip()
            b, e = r.get("beginMon"), r.get("endMon")
            if not op or not b:
                continue
            mon = f"{b}월" if b == e else f"{b}~{e}월"
            key = (mon, op)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {mon}: {op}")
    if not lines:
        return ""
    return (
        "농사로 공식 농작업일정(권위 있는 표준 시기 — 여러 작기가 섞여 있으니, "
        "사용자 시작일에 가장 가까운 작기를 골라 그 작기의 파종·정식·수확 시기를 "
        "그대로 따르세요):\n" + "\n".join(lines) + "\n\n"
    )


# 씨뿌림/정식 계열 vs 수확 계열 작업명 키워드.
_SEED_KW = ("씨뿌림", "파종", "직파", "아주심기", "정식", "옮겨")
_HARVEST_KW = ("수확",)


def _seasons_for(slug: str) -> list[tuple[int, int]]:
    """작기별 (씨뿌림월, 수확월) 목록. 농작업일정 행을 헤더(▶) 기준으로 묶는다.

    원본 정렬이 일부 섞여 있어도, 한 블록의 '첫 씨뿌림월·첫 수확월'은 보통 같은
    작기의 쌍이라 ~2-3개월 간격으로 합리적으로 잡힌다.
    """
    cntnts = _slug_to_cntnts().get(slug)
    if not cntnts:
        return []
    fw = _farmwork()
    seasons: list[dict[str, int | None]] = []
    cur: dict[str, int | None] | None = None
    for no in cntnts:
        entry = fw.get(no)
        if not entry:
            continue
        for r in entry.get("rows", []):
            if r.get("infoSeCode") != _MAIN_WORK:
                continue
            op = (r.get("opertNm") or "").strip()
            if not op:
                continue
            try:
                bm = int(r.get("beginMon"))
            except (TypeError, ValueError):
                continue
            if "▶" in op:  # 작기 헤더 — 새 블록 시작
                cur = {"seed": None, "harvest": None}
                seasons.append(cur)
                continue
            if cur is None:
                cur = {"seed": None, "harvest": None}
                seasons.append(cur)
            if any(k in op for k in _HARVEST_KW):
                if cur["harvest"] is None:
                    cur["harvest"] = bm
            elif any(k in op for k in _SEED_KW):
                if cur["seed"] is None:
                    cur["seed"] = bm
    return [
        (s["seed"], s["harvest"])
        for s in seasons
        if s["seed"] is not None and s["harvest"] is not None
    ]


def harvest_offset_for(slug: str, start: date) -> int | None:
    """시작일에 가장 가까운 작기의 씨뿌림→수확 기간(일)을 추정.

    농작업일정의 작기별 (씨뿌림월, 수확월) 중 시작 월에 가장 가까운 작기를 골라
    개월 차 × 30일로 환산한다. day_offset 은 시작일(=대략 파종 시점) 기준이라
    이 값이 곧 권위 있는 수확 offset 이다. 데이터가 없거나 비정상이면 None.
    """
    seasons = _seasons_for(slug)
    if not seasons:
        return None
    sm = start.month

    def circ(a: int, b: int) -> int:
        d = abs(a - b) % 12
        return min(d, 12 - d)

    seed, harvest = min(seasons, key=lambda sh: circ(sh[0], sm))
    gap = (harvest - seed) % 12
    if gap == 0:
        return None  # 같은 달 씨뿌림·수확은 데이터 이상 — 보정 근거로 쓰지 않음
    return gap * 30
