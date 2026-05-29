"""OpenAI 기반 작목별 추천 이유 생성.

작목·지역·모드별로 서로 다른 한두 문장짜리 추천 이유를 생성한다.
OPENAI_API_KEY 가 없거나 호출 실패 시 engine 의 템플릿 llmReason 으로 fallback.

캐시: (cropId, region, mode, facility) 키로 동일 입력 반복 호출을 막는다.
"""

import asyncio

from openai import AsyncOpenAI

from app.config import settings
from app.schemas.recommend import CropRecommendationItem, OnboardingInput

_MODEL = "gpt-4o-mini"
_TIMEOUT_S = 8.0
_cache: dict[tuple[str, str, str, str], str] = {}
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    """API 키가 있을 때만 클라이언트를 1회 생성해 재사용."""
    global _client
    if not settings.openai_api_key:
        return None
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=_TIMEOUT_S)
    return _client


def _cache_key(item: CropRecommendationItem, input_: OnboardingInput) -> tuple[str, str, str, str]:
    return (item.cropId, input_.region, input_.mode, input_.facility or "-")


def _build_prompt(item: CropRecommendationItem, input_: OnboardingInput) -> str:
    region = input_.province or input_.region
    if input_.mode == "returning":
        ctx = (
            f"귀농 모드. 지역: {region} {input_.region}, 시설: {input_.facility or '노지'}, "
            f"예상 연매출 약 {item.expectedRevenueManwon}만원, 적합도 {item.matchScore}%."
        )
        ask = "귀농 정착 관점(수익성·재배 난이도·지역 기후 적합성)에서"
    else:
        ctx = (
            f"주말농장 모드. 지역: {region} {input_.region}, 방문빈도: {input_.visitFrequency or '주1회'}, "
            f"예상 수확량 약 {item.expectedYieldKg}kg, 직거래 단가 {item.expectedDirectPriceWon}원/kg, "
            f"적합도 {item.matchScore}%."
        )
        ask = "주말 방문 관리 관점(관리 편의성·가족 소비·직거래 적합성)에서"
    return (
        f"작목: {item.name}\n특징 태그: {', '.join(item.tags)}\n{ctx}\n\n"
        f"위 작목을 {region} 지역에서 추천하는 이유를 {ask} "
        f"한국어 1~2문장(80자 이내)으로, 다른 작목과 구별되는 이 작목만의 강점을 담아 작성해줘. "
        f"수치를 단정적으로 반복하지 말고 자연스럽게. 따옴표 없이 본문만."
    )


async def generate_reason(item: CropRecommendationItem, input_: OnboardingInput) -> str:
    """작목 추천에 대한 자연어 이유 생성. 실패 시 템플릿 llmReason 으로 fallback."""
    client = _get_client()
    if client is None:
        return item.llmReason

    key = _cache_key(item, input_)
    if key in _cache:
        return _cache[key]

    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "너는 한국 귀농·주말농장 작목 추천 전문가다. 간결하고 신뢰감 있게 설명한다.",
                },
                {"role": "user", "content": _build_prompt(item, input_)},
            ],
            max_tokens=160,
            temperature=0.7,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return item.llmReason
        _cache[key] = text
        return text
    except Exception:
        # 네트워크·쿼터·인증 오류 등 어떤 경우에도 추천 자체는 동작해야 하므로 fallback.
        return item.llmReason


async def attach_reasons(
    items: list[CropRecommendationItem], input_: OnboardingInput
) -> list[CropRecommendationItem]:
    """여러 작목의 추천 이유를 동시에 생성해 llmReason 을 교체한 새 리스트를 반환."""
    reasons = await asyncio.gather(*(generate_reason(it, input_) for it in items))
    return [it.model_copy(update={"llmReason": r}) for it, r in zip(items, reasons, strict=True)]
