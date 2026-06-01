"""정부 지원사업 매칭.

온보딩 조건(귀농/주말, 연령, 영농경력)으로 각 사업의 자격을 판정한다.
  - eligible: 하드조건(모드·연령·경력) 모두 충족
  - check:    일부 조건이 미상(예: 연령 미입력)이라 확인 필요
  - excluded: 명확히 대상 아님 (목록에서 제외, 개수만 노출)

조건은 매년 바뀌므로 결과는 참고용. 정렬은 적합 우선 + 카테고리 가중.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.data import gov_support

# 카테고리 노출 우선순위 (현금성·핵심 자금 먼저)
_CATEGORY_RANK = {
    "정착지원금": 0,
    "융자": 1,
    "주거": 2,
    "농지": 3,
    "교육": 4,
    "인증": 5,
    "임대": 6,
    "보험": 7,
    "복지": 8,
}


@dataclass
class MatchedProgram:
    program: dict[str, Any]
    status: str          # 'eligible' | 'check'
    reasons: list[str]   # 충족/확인 사유


def _eval(prog: dict, mode: str, age: int | None, farming_years: int | None) -> tuple[str | None, list[str]]:
    """(status, reasons). status None=대상아님(제외)."""
    el = prog.get("eligibility", {})
    reasons: list[str] = []
    uncertain = False

    modes = el.get("modes") or []
    if modes and mode not in modes:
        return None, []
    if modes:
        reasons.append("귀농 대상" if mode == "returning" else "주말농장 대상")

    amin, amax = el.get("age_min"), el.get("age_max")
    if amin is not None or amax is not None:
        if age is None:
            uncertain = True
            lo = amin if amin is not None else ""
            hi = amax if amax is not None else ""
            reasons.append(f"연령 조건({lo}~{hi}세) 확인 필요")
        else:
            if amin is not None and age < amin:
                return None, []
            if amax is not None and age > amax:
                return None, []
            reasons.append(f"연령 {age}세 충족")

    fmax = el.get("farming_max_years")
    if fmax is not None:
        if farming_years is None:
            uncertain = True
            reasons.append(f"영농경력 {fmax}년 이하 조건 확인 필요")
        elif farming_years > fmax:
            return None, []
        else:
            reasons.append(f"영농경력 {farming_years}년 충족")

    return ("check" if uncertain else "eligible"), reasons


def match_programs(
    *, mode: str, age: int | None = None, farming_years: int | None = None
) -> tuple[list[MatchedProgram], int]:
    """(매칭 결과, 제외 개수). 적합 우선, 카테고리 가중으로 정렬."""
    matched: list[MatchedProgram] = []
    excluded = 0
    for prog in gov_support.load_programs():
        status, reasons = _eval(prog, mode, age, farming_years)
        if status is None:
            excluded += 1
            continue
        matched.append(MatchedProgram(program=prog, status=status, reasons=reasons))

    matched.sort(
        key=lambda m: (
            0 if m.status == "eligible" else 1,
            _CATEGORY_RANK.get(m.program.get("category", ""), 9),
            m.program.get("id", 999),
        )
    )
    return matched, excluded
