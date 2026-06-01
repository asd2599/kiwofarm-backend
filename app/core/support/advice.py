"""GPT-4o 지원사업 맞춤 요약.

방침(다른 advice 와 동일): LLM 은 설명기. 매칭 결과(사업명·자격상태)만 근거로
사용자 조건에 맞춘 2~3문장 요약·우선순위 안내를 만든다. 키 없음/실패 시 규칙 폴백.
"""

from __future__ import annotations

from app.config import settings
from app.core.support.match import MatchedProgram

_MODEL = "gpt-4o"
_cache: dict[tuple, tuple[str, str]] = {}

DISCLAIMER = "지원 조건은 매년·지자체별로 달라지므로 최종 자격은 해당 공고로 확인하세요."

SYSTEM_PROMPT = (
    "너는 한국 귀농·농업인 정책자금 안내자다. "
    "주어진 매칭된 지원사업 목록과 사용자 조건만 근거로 판단하고 새 사업을 지어내지 않는다. "
    "한국어로 2~3문장, 단정적이고 간결하게 쓰되 느낌표는 쓰지 않는다. "
    "사용자 조건에서 가장 핵심이 되는 사업 1~2개를 먼저 짚고 왜 우선인지 설명한다. "
    "현금성 지원(정착지원금)·저리 융자를 우선순위로 둔다. "
    f"마지막에 '{DISCLAIMER}'를 덧붙인다."
)


def _profile(mode: str, age: int | None, province: str | None) -> str:
    parts = ["귀농 예정" if mode == "returning" else "주말농장"]
    if age is not None:
        parts.append(f"만 {age}세")
    if province:
        parts.append(province)
    return ", ".join(parts)


def _fmt(mode: str, age: int | None, province: str | None, matched: list[MatchedProgram]) -> str:
    lines = [f"사용자: {_profile(mode, age, province)}", "매칭된 지원사업:"]
    for m in matched[:8]:
        p = m.program
        tag = "적합" if m.status == "eligible" else "조건확인"
        lines.append(f"- [{tag}] {p['name']} ({p['category']}, {p['support']})")
    return "\n".join(lines)


def _rule_based(mode: str, age: int | None, province: str | None, matched: list[MatchedProgram]) -> str:
    if not matched:
        return f"입력 조건에 맞는 지원사업을 찾지 못했습니다. 조건을 넓혀 다시 확인해 보세요. {DISCLAIMER}"
    eligible = [m for m in matched if m.status == "eligible"]
    head = eligible or matched
    top = head[0].program
    second = head[1].program["name"] if len(head) >= 2 else None
    profile = _profile(mode, age, province)
    msg = f"{profile} 조건에는 '{top['name']}'({top['support']})가 우선 검토 대상입니다."
    if second:
        msg += f" '{second}'도 함께 신청을 고려할 만합니다."
    msg += f" 총 {len(matched)}개 사업이 매칭됐습니다. {DISCLAIMER}"
    return msg


def _cache_key(mode: str, age: int | None, province: str | None, matched: list[MatchedProgram]) -> tuple:
    return (mode, age, province, tuple((m.program["id"], m.status) for m in matched))


async def generate_support_advice(
    mode: str, age: int | None, province: str | None, matched: list[MatchedProgram]
) -> tuple[str, str]:
    """(요약 텍스트, source). source = 'ai' | 'rule'."""
    key = _cache_key(mode, age, province, matched)
    if key in _cache:
        return _cache[key]

    if not settings.openai_api_key or not matched:
        result = (_rule_based(mode, age, province, matched), "rule")
        _cache[key] = result
        return result

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _fmt(mode, age, province, matched)},
            ],
            temperature=0.3,
            max_tokens=280,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty completion")
        result = (text, "ai")
    except Exception:
        result = (_rule_based(mode, age, province, matched), "rule")

    _cache[key] = result
    return result
