from fastapi import APIRouter

from app.core.recommend.engine import recommend
from app.core.recommend.llm_reason import attach_reasons
from app.schemas.recommend import OnboardingInput, RecommendationResponse

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendationResponse)
async def post_recommend(payload: OnboardingInput) -> RecommendationResponse:
    """온보딩 입력으로 TOP-3 작목 추천.

    engine 의 룰베이스 점수화로 작목을 가린 뒤, llm_reason 으로 작목별
    추천 이유를 OpenAI 로 생성한다(키 없거나 실패 시 템플릿 fallback).

    TODO: core.recommend.engine 의 XGBoost 추론 연결.
    """
    result = recommend(payload)
    items = await attach_reasons(result.items, payload)
    return RecommendationResponse(mode=result.mode, items=items)
