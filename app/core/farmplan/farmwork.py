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


@lru_cache(maxsize=1)
def _slug_to_days() -> dict[str, tuple[int, int]]:
    """slug → (최소, 최대) 심은 뒤 수확까지 일수. crops_master days_to_harvest.

    40종 작물 마스터의 권위 있는 생육기간. 농작업일정 '월차×30' 환산보다 정확해
    수확 시기 보정·프롬프트 주입의 1차 근거로 쓴다.
    """
    with open(_DATA / "crops_master.json", encoding="utf-8") as f:
        data = json.load(f)
    crops = data.get("crops", []) if isinstance(data, dict) else data
    out: dict[str, tuple[int, int]] = {}
    for c in crops:
        if not isinstance(c, dict):
            continue
        slug = c.get("id")
        dth = c.get("days_to_harvest")
        if not slug or not isinstance(dth, list) or len(dth) != 2:
            continue
        try:
            lo, hi = int(dth[0]), int(dth[1])
        except (TypeError, ValueError):
            continue
        if 0 < lo <= hi:
            out[slug] = (lo, hi)
    return out


def days_to_harvest_for(slug: str) -> tuple[int, int] | None:
    """slug 의 (최소, 최대) 수확 소요일. 마스터에 없으면 None."""
    return _slug_to_days().get(slug)


@lru_cache(maxsize=1)
def _slug_to_water() -> dict[str, str]:
    """slug → water_need('많음'|'보통'|'적음'). crops_master."""
    with open(_DATA / "crops_master.json", encoding="utf-8") as f:
        data = json.load(f)
    crops = data.get("crops", []) if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for c in crops:
        if isinstance(c, dict) and c.get("id") and c.get("water_need"):
            out[c["id"]] = str(c["water_need"]).strip()
    return out


# 물 수요별 관수 간격(일). (노지, 화분) — 화분·플랜터는 흙이 적어 빨리 말라 더 자주.
# 기준점: 토마토(많음·노지) ≈ 6일(사용자 "2주에 두번"). 값은 여기서 쉽게 조정 가능.
_WATER_INTERVAL: dict[str, tuple[int, int]] = {
    "많음": (6, 3),
    "보통": (9, 5),
    "적음": (14, 8),
}
_WATER_DEFAULT = (9, 5)  # water_need 미상 작물(보통 취급)


def water_interval_for(slug: str, pot: bool) -> int:
    """slug·재배장소 기준 관수 간격(일). 마스터에 없으면 보통(노지7/화분4)."""
    need = _slug_to_water().get(slug, "보통")
    field_i, pot_i = _WATER_INTERVAL.get(need, _WATER_DEFAULT)
    return pot_i if pot else field_i


def growth_period_block(slug: str) -> str:
    """수확까지 생육기간을 프롬프트용 텍스트로 — GPT 가 수확 offset 을 직접 맞추게.

    days_to_harvest 가 가장 정확하므로 첫 수확≈lo, 마무리≈hi 로 못박는다. 없으면 ''.
    """
    dth = _slug_to_days().get(slug)
    if not dth:
        return ""
    lo, hi = dth
    return (
        f"이 작물은 심은(파종·정식) 뒤 수확까지 보통 {lo}~{hi}일 걸립니다. "
        f"첫 수확 작업의 day_offset 은 약 {lo}일, 수확 마무리는 약 {hi}일에 맞추고, "
        "이보다 훨씬 짧게(예: 한 달 만에 끝나게) 잡지 마세요.\n\n"
    )


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
    """시작일 기준 권위 있는 수확 offset(일) 추정. 보정의 기준값.

    1순위: crops_master days_to_harvest(심은 뒤 수확까지 일수)의 중앙값 — 가장 정확.
    2순위: 농작업일정의 작기별 (씨뿌림월, 수확월) 개월 차 × 30일.
    day_offset 은 시작일(=대략 파종 시점) 기준이라 이 값이 곧 수확 offset 이다.
    둘 다 없거나 비정상이면 None.
    """
    dth = _slug_to_days().get(slug)
    if dth:
        lo, hi = dth
        return (lo + hi) // 2

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
