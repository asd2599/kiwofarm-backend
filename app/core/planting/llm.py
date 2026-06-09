"""심기 추천 AI 설명 — "AI는 설명, 매트릭스는 결정" (§4.2, 부록 D).

추천된 top N 작물의 매트릭스 근거 + 사용자 입력을 컨텍스트로 gpt-4o-mini 가
{reason, tips[], first_month_todo[]} 만 생성한다. 1회 배치 호출(작물별 분리 호출
지양). 키 없거나 실패 시 ai_explain 없이(None) 추천 자체는 그대로 동작.
"""

from __future__ import annotations

import json

from openai import AsyncOpenAI

from app.config import settings
from app.schemas.planting import AiExplain, PlantingInput, RecommendationItem

_MODEL = "gpt-4o-mini"
_TIMEOUT_S = 12.0
_client: AsyncOpenAI | None = None

_SYS = (
    "당신은 텃밭 초보를 돕는 한국어 작목 상담사입니다. "
    "규칙: 제공된 '추천 데이터'와 '작물 캘린더'에 근거해서만 답하세요. "
    "재배 시기·수치는 데이터 값만 사용하고 임의로 지어내지 마세요. 모르면 비워두세요. "
    "초보도 이해할 쉬운 말로, 단정·과장 없이 참고용으로 작성하세요."
)


def _get_client() -> AsyncOpenAI | None:
    global _client
    if not settings.openai_api_key:
        return None
    if _client is None:
        # max_retries=0: 재시도 시 _TIMEOUT_S 가 3배(타임아웃×3)로 누적돼 추천
        # 응답이 30초+로 늘어나 프론트(10s) 타임아웃을 넘긴다. AI 설명은 부가
        # 정보일 뿐이므로 한 번 시도 후 실패하면 즉시 fallback(설명 생략)한다.
        _client = AsyncOpenAI(
            api_key=settings.openai_api_key, timeout=_TIMEOUT_S, max_retries=0
        )
    return _client


def _crop_context(item: RecommendationItem, month: int) -> dict:
    acts = [
        {"action": a.action, "method": a.method, "label": a.label}
        for a in item.calendar_this_month
    ]
    return {
        "crop_id": item.crop_id,
        "name": item.name,
        "category": item.category,
        "difficulty": item.difficulty,
        "days_to_harvest": item.days_to_harvest,
        "plantable_now": item.plantable_now,
        "this_month_actions": acts,
        "reasons": item.reasons,
        "source": item.source,
    }


async def attach_ai_explain(
    items: list[RecommendationItem], inp: PlantingInput, month: int
) -> list[RecommendationItem]:
    """top N 작물에 ai_explain 을 붙인 새 리스트 반환. 실패 시 원본 그대로."""
    client = _get_client()
    if client is None or not items:
        return items

    user_ctx = {
        "user_input": {
            "지역": inp.sigungu,
            "장소": inp.place,
            "방향": inp.direction,
            "일조": inp.sun_hours,
            "경험": inp.experience,
            "관리빈도": f"주 {len(inp.visitDays)}회 방문" if inp.visitDays else None,
            "현재월": month,
        },
        "recommendations": [_crop_context(it, month) for it in items],
    }
    schema = (
        '{"explains": [{"crop_id": "...", "reason": "왜 이 사용자에게 맞는지 1~2문장", '
        '"tips": ["이 환경 맞춤 주의/팁", "..."], '
        '"first_month_todo": ["첫 달 할 일", "..."]}]}'
    )
    user = (
        f"[추천 데이터]\n{json.dumps(user_ctx, ensure_ascii=False)}\n\n"
        f"각 작물(crop_id)에 대해 이 사용자 환경 맞춤 설명을 만들어줘. "
        f"reason 은 80자 이내, tips·first_month_todo 는 각 항목 30자 이내, 1~3개. "
        f"this_month_actions 의 행동(파종/정식 등)만 근거로 쓰고 새 시기를 지어내지 마. "
        f"아래 JSON 스키마로만(코드블록 금지):\n{schema}"
    )

    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "system", "content": _SYS}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0.5,
            max_tokens=1400,
        )
        parsed = json.loads(resp.choices[0].message.content or "{}")
    except Exception:
        return items

    by_id: dict[str, AiExplain] = {}
    for e in parsed.get("explains", []) if isinstance(parsed.get("explains"), list) else []:
        if not isinstance(e, dict) or "crop_id" not in e:
            continue
        by_id[e["crop_id"]] = AiExplain(
            reason=str(e.get("reason", "")),
            tips=[str(t) for t in e.get("tips", []) if t][:3],
            first_month_todo=[str(t) for t in e.get("first_month_todo", []) if t][:3],
        )
    return [it.model_copy(update={"ai_explain": by_id.get(it.crop_id)}) for it in items]
