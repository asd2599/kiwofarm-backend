"""GPT-4o 출하 조언 생성.

방침: LLM 은 예측기가 아니라 설명기. 코드가 계산한 피처(features.py)만 해석해
자연어 출하 조언을 만든다. 키 없음/호출 실패 시 규칙기반으로 폴백.
"""

from __future__ import annotations

from app.config import settings
from app.core.shipping.features import ShippingFeatures

_MODEL = "gpt-4o"
_cache: dict[tuple, tuple[str, str]] = {}

SYSTEM_PROMPT = (
    "너는 한국 농산물 도매 출하 시점 조언자다. "
    "주어진 KAMIS 도매가 지표와 예측치만 근거로 판단하고, 새로운 수치를 임의로 만들지 않는다. "
    "한국어로 2~3문장, 단정적이고 간결하게 쓰되 느낌표는 쓰지 않는다. "
    "현재가·최근 추세·작년/평년 대비와 함께 '향후 예측'을 핵심 근거로 해석한 뒤, "
    "'지금 출하 권장' 또는 '출하 대기 권장' 중 하나를 분명히 제시한다. "
    "예측상 추가 하락이면 지금 출하, 추가 상승이면 대기 쪽으로 무게를 둔다. "
    "마지막에 'KAMIS 도매가 기반 참고 수치로 실제 시장 상황에 따라 달라질 수 있습니다.'를 덧붙인다."
)


def _fmt_features(f: ShippingFeatures) -> str:
    lines = [f"작물: {f.crop_name}"]
    if f.current_price is not None:
        lines.append(f"현재 도매가: {f.current_price:,}원/{f.unit}")
    if f.vs_prev_pct is not None:
        lines.append(f"전일 대비: {f.vs_prev_pct:+.1f}%")
    if f.trend_pct is not None:
        lines.append(f"최근 한 달 추세: {f.trend_pct:+.1f}% ({f.direction})")
    if f.vs_year_ago_pct is not None:
        lines.append(f"작년 동기 대비: {f.vs_year_ago_pct:+.1f}%")
    if f.vs_normal_pct is not None:
        lines.append(f"평년 대비: {f.vs_normal_pct:+.1f}%")
    if f.volatility_pct is not None:
        lines.append(f"최근 변동성: {f.volatility_pct:.1f}%")
    if f.period_high is not None and f.period_low is not None:
        lines.append(f"기간 최고/최저: {f.period_high:,}/{f.period_low:,}원")
    if f.forecast_price is not None:
        pct = f" ({f.forecast_pct:+.1f}%)" if f.forecast_pct is not None else ""
        lines.append(f"{f.forecast_days}일 후 예측가: {f.forecast_price:,}원{pct}")
    return "\n".join(lines)


def _rule_based(f: ShippingFeatures) -> str:
    """LLM 미사용 시 폴백 조언."""
    name = f.crop_name
    price = f"{f.current_price:,}원/{f.unit}" if f.current_price is not None else "현재가 미상"
    disclaimer = "KAMIS 도매가 기반 참고 수치로 실제 시장 상황에 따라 달라질 수 있습니다."

    fp = f.forecast_pct
    if fp is not None and fp <= -3:
        rec = f"{f.forecast_days}일 후 {fp:+.1f}% 하락이 예측돼 추가 하락 전 출하가 유리합니다. 지금 출하 권장."
    elif fp is not None and fp >= 3:
        rec = f"{f.forecast_days}일 후 {fp:+.1f}% 상승이 예측돼 단기 보관이 유리할 수 있습니다. 출하 대기 권장."
    elif f.direction == "상승":
        rec = "최근 상승세라 단기 보관 후 출하를 고려할 수 있습니다. 출하 대기 권장."
    elif f.direction == "하락":
        rec = "하락세가 이어지고 있어 추가 하락 전 빠른 출하가 유리합니다. 지금 출하 권장."
    else:
        rec = "가격이 안정적이라 보관 비용을 고려하면 현 시점 출하가 무난합니다. 지금 출하 권장."

    ctx = []
    if f.vs_year_ago_pct is not None:
        ctx.append(f"작년 동기 대비 {f.vs_year_ago_pct:+.1f}%")
    if f.vs_normal_pct is not None:
        ctx.append(f"평년 대비 {f.vs_normal_pct:+.1f}%")
    ctx_str = (", ".join(ctx) + " 수준입니다. ") if ctx else ""

    return f"{name} 도매가는 현재 {price}이며 {ctx_str}{rec} {disclaimer}"


def _cache_key(f: ShippingFeatures) -> tuple:
    return (
        f.crop_name,
        f.current_price,
        f.direction,
        f.vs_year_ago_pct,
        f.vs_normal_pct,
        f.forecast_pct,
    )


async def generate_shipping_advice(f: ShippingFeatures) -> tuple[str, str]:
    """(조언 텍스트, source) 반환. source = 'ai' | 'rule'."""
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
                {"role": "user", "content": _fmt_features(f)},
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
