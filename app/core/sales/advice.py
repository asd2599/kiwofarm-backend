"""GPT-4o 판매 채널 추천 조언.

방침(출하 조언과 동일): LLM 은 예측기가 아니라 설명기. 코드가 계산한 채널별
실수령액·운송비·거리만 근거로 '어디서 파는 게 유리한지'를 자연어로 추천한다.
키 없음/호출 실패 시 규칙기반 폴백.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings

_MODEL = "gpt-4o"
_cache: dict[tuple, tuple[str, str]] = {}


@dataclass
class ChannelFeature:
    key: str            # 'direct' | 'wholesale'
    label: str
    net: int | None             # 가격 실수령 (운송 전)
    place_name: str | None
    distance_km: float | None
    transport_cost: int | None
    net_after: int | None       # 운송비 차감 최종


@dataclass
class SalesFeatures:
    crop_name: str
    amount: float | None
    amount_unit: str
    channels: list[ChannelFeature]
    best_key: str | None
    delta_after: int | None     # 1위-2위 최종 실수령 차액


DISCLAIMER = "시장가·통상 수수료·거리 기반 운송비 추정으로 산출한 참고 수치입니다."

SYSTEM_PROMPT = (
    "너는 한국 소규모 농가의 판매 채널 조언자다. "
    "'직매장 직거래'와 '도매시장 출하' 두 채널의 예상 실수령액과 운송비(왕복 거리 기반)를 "
    "근거로만 판단하고 새로운 수치를 만들지 않는다. "
    "한국어로 2~3문장, 단정적이고 간결하게 쓰되 느낌표는 쓰지 않는다. "
    "가격(실수령)과 운송비의 트레이드오프를 짚고, 어느 채널이 유리한지 분명히 추천한다. "
    "운송비를 빼고도 실수령이 높은 쪽을 우선하되, 거리가 멀어 운송비가 크면 그 점을 지적한다. "
    f"마지막에 '{DISCLAIMER}'를 덧붙인다."
)


def _won(v: int | None) -> str:
    return f"{v:,}원" if v is not None else "미상"


def _fmt(f: SalesFeatures) -> str:
    lines = [f"작물: {f.crop_name}", f"판매량: {f.amount}{f.amount_unit}"]
    for c in f.channels:
        seg = [f"[{c.label}]"]
        seg.append(f"가격 실수령 {_won(c.net)}")
        if c.place_name is not None:
            seg.append(f"가까운 곳 {c.place_name}({c.distance_km}km)")
        if c.transport_cost is not None:
            seg.append(f"운송비 {_won(c.transport_cost)}")
        seg.append(f"운송 후 최종 {_won(c.net_after)}")
        lines.append(" / ".join(seg))
    return "\n".join(lines)


def _rule_based(f: SalesFeatures) -> str:
    rated = [c for c in f.channels if c.net_after is not None]
    if not rated:
        return f"{f.crop_name}의 채널별 실수령을 계산할 데이터가 부족합니다. {DISCLAIMER}"
    rated.sort(key=lambda c: c.net_after or 0, reverse=True)
    win = rated[0]
    parts = [f"{f.crop_name} {f.amount}{f.amount_unit} 기준, 운송비까지 반영하면 {win.label}이 유리합니다."]
    if len(rated) >= 2 and f.delta_after is not None:
        lose = rated[1]
        parts.append(f"{win.label} 최종 {_won(win.net_after)} vs {lose.label} {_won(lose.net_after)}로 약 {_won(f.delta_after)} 더 남습니다.")
        if win.transport_cost is not None and lose.transport_cost is not None and lose.transport_cost > win.transport_cost:
            parts.append(f"{lose.label}은 거리가 멀어 운송비({_won(lose.transport_cost)})가 부담입니다.")
    parts.append(DISCLAIMER)
    return " ".join(parts)


def _cache_key(f: SalesFeatures) -> tuple:
    return (
        f.crop_name, f.amount, f.amount_unit, f.best_key, f.delta_after,
        tuple((c.key, c.net, c.transport_cost, c.net_after) for c in f.channels),
    )


async def generate_sales_advice(f: SalesFeatures) -> tuple[str, str]:
    """(조언 텍스트, source). source = 'ai' | 'rule'."""
    key = _cache_key(f)
    if key in _cache:
        return _cache[key]

    if not settings.openai_api_key:
        result = (_rule_based(f), "rule")
        _cache[key] = result
        return result

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _fmt(f)},
            ],
            temperature=0.3,
            max_tokens=260,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty completion")
        result = (text, "ai")
    except Exception:
        result = (_rule_based(f), "rule")

    _cache[key] = result
    return result
