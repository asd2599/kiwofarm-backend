"""OpenAI 기반 작목별 추천 이유 생성.

작목·지역·모드별로 서로 다른 한두 문장짜리 추천 이유를 생성한다.
OPENAI_API_KEY 가 없거나 호출 실패 시 engine 의 템플릿 llmReason 으로 fallback.

캐시: (cropId, region, mode, facility) 키로 동일 입력 반복 호출을 막는다.
"""

import asyncio

from openai import AsyncOpenAI

from app.config import settings
from app.core.rag import retrieve as rag_retrieve
from app.core.recommend.engine import item_code_for
from app.schemas.recommend import CropRecommendationItem, OnboardingInput

# 농사로 farminfo 에서 추천 이유 근거로 쓸 재배 컨텍스트 질의/분량.
_CONTEXT_QUERY = "재배 환경 기후 토양 적합성 재배 난이도 관리 포인트 병해충 수익성"
_CONTEXT_K = 6
_CONTEXT_MAX_CHARS = 1500  # 프롬프트 비대화 방지
# 이달의 농업기술(monthtech, 작물특화)을 주간 회보(weekfarm, 다작물)보다 우선시키는 가중.
_MONTHTECH_BOOST = 0.08

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


def _build_prompt(
    item: CropRecommendationItem,
    input_: OnboardingInput,
    cultivation_context: str = "",
) -> str:
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
    # 추천 이유는 농사로 농업기술 자료(RAG)를 1차 근거로 삼는다. 낡은 우수농가
    # 실측치(peerEvidence)는 더 이상 이유 프롬프트에 넣지 않는다(별도 카드로만 노출).
    if cultivation_context:
        prompt = (
            f"작목: {item.name}\n특징 태그: {', '.join(item.tags)}\n{ctx}\n\n"
            f"[농사로 농업기술 참고자료]\n{cultivation_context}\n\n"
            f"위 참고자료를 근거로 {region} 지역에서 이 작목을 {ask} 추천하는 이유를 "
            f"한국어 1~2문장(110자 이내)으로 작성해줘. 참고자료의 구체적 재배 포인트"
            f"(재배 적기·관리·병해충·품질 등) 한 가지를 자연스럽게 녹여 다른 작목과 구별되는 "
            f"강점을 보여줘. 자료에 없는 내용은 지어내지 말고, 따옴표 없이 본문만."
        )
    else:
        prompt = (
            f"작목: {item.name}\n특징 태그: {', '.join(item.tags)}\n{ctx}\n\n"
            f"위 작목을 {region} 지역에서 추천하는 이유를 {ask} "
            f"한국어 1~2문장(80자 이내)으로 다른 작목과 구별되는 "
            f"이 작목만의 강점을 담아 작성해줘. "
            f"수치를 단정적으로 반복하지 말고 자연스럽게. 따옴표 없이 본문만."
        )
    return prompt


async def generate_reason(
    item: CropRecommendationItem,
    input_: OnboardingInput,
    cultivation_context: str = "",
) -> str:
    """작목 추천에 대한 자연어 이유 생성. 실패 시 템플릿 llmReason 으로 fallback."""
    client = _get_client()
    if client is None:
        return item.llmReason

    key = _cache_key(item, input_)
    # cultivation_context 가 있으면 프롬프트가 달라지므로 캐시를 우회한다
    # (없을 때만 기존 캐시 사용 — 기본 동작 보존).
    if not cultivation_context and key in _cache:
        return _cache[key]

    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "너는 한국 귀농·주말농장 작목 추천 전문가다. 간결하고 신뢰감 있게 설명한다.",
                },
                {
                    "role": "user",
                    "content": _build_prompt(item, input_, cultivation_context),
                },
            ],
            max_tokens=256,
            temperature=0.7,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return item.llmReason
        if not cultivation_context:
            _cache[key] = text
        return text
    except Exception:
        # 네트워크·쿼터·인증 오류 등 어떤 경우에도 추천 자체는 동작해야 하므로 fallback.
        return item.llmReason


async def _fetch_cultivation_context(crop_id: str) -> str:
    """작목의 농사로 farminfo(이달의+주간) 에서 재배 컨텍스트 회수. 없으면 빈 문자열.

    itemCode 단위로 store 에 적재된 farminfo 를 retrieve 로 조회한다. 인제스트/GPT
    호출 없이 임베딩 검색만 하므로 추천 응답을 느리게 하지 않는다. 키 미설정·미적재·
    매핑 없음 등 어떤 경우에도 빈 문자열로 수렴(추천은 그대로 동작).
    """
    item_code = item_code_for(crop_id)
    if not item_code:
        return ""
    try:
        chunks = await rag_retrieve.retrieve_boosted(
            item_code, _CONTEXT_QUERY, k=_CONTEXT_K, boost={"monthtech": _MONTHTECH_BOOST}
        )
    except Exception:
        return ""
    return "\n\n".join(chunks)[:_CONTEXT_MAX_CHARS]


async def attach_reasons(
    items: list[CropRecommendationItem], input_: OnboardingInput
) -> list[CropRecommendationItem]:
    """여러 작목의 추천 이유를 동시에 생성해 llmReason 을 교체한 새 리스트를 반환.

    각 작목의 농사로 farminfo 재배 컨텍스트를 조회해 그 작목 프롬프트에만 주입한다
    (작목별 근거). 컨텍스트가 없으면 기존 템플릿 기반 이유로 동작.
    """
    contexts = await asyncio.gather(*(_fetch_cultivation_context(it.cropId) for it in items))
    reasons = await asyncio.gather(
        *(
            generate_reason(it, input_, ctx)
            for it, ctx in zip(items, contexts, strict=True)
        )
    )
    return [it.model_copy(update={"llmReason": r}) for it, r in zip(items, reasons, strict=True)]
