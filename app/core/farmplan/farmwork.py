"""농사로 공식 농작업일정(raw_farmwork.json) 주입 — 캘린더 생성의 권위 있는 시기 근거.

cultivation 임베딩이 GPT 생성(source=general)이라 시기가 부정확했다. 정작 정확한
파종/정식/수확 월 데이터(농사로 farmWorkingPlanNew)는 raw_farmwork.json 에 있으나
생성에 안 쓰이고 있었다. slug→cntntsNo(crops_master)→주요농작업(infoSeCode 410001)
행을 프롬프트에 직접 넣어 GPT 가 이 시기를 따르게 한다.
"""

from __future__ import annotations

import json
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
