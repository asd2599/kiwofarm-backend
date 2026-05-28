from fastapi import APIRouter

from app.core.recommend.engine import recommend
from app.schemas.recommend import OnboardingInput, RecommendationResponse

router = APIRouter(prefix="/recommend", tags=["recommend"])


@router.post("", response_model=RecommendationResponse)
async def post_recommend(payload: OnboardingInput) -> RecommendationResponse:
    """온보딩 입력으로 TOP-3 작목 추천.

    TODO:
      - core.recommend.engine 의 XGBoost 추론 연결
      - core.recommend.llm_reason 으로 자연어 이유 비동기 생성·캐시
    """
    return recommend(payload)
