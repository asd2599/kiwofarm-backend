"""GPT-4o 추천 이유 생성.

TODO 실구현:
  - OpenAI 클라이언트 (openai_api_key from settings)
  - 시스템 프롬프트: 우수농가 데이터·기후·시장가격 근거를 한 문단으로
  - 사용자 입력 + 매칭 스코어 + 핵심 통계만 컨텍스트로 전달
  - 응답 캐시 (cropId + region + facility 키)
"""

from app.schemas.recommend import CropRecommendationItem, OnboardingInput


async def generate_reason(item: CropRecommendationItem, input_: OnboardingInput) -> str:
    """주어진 작목 추천에 대해 자연어 이유를 생성한다.

    현재 구현: engine.py 의 시드 llmReason 을 그대로 사용. GPT-4o 연동 전 placeholder.
    """
    return item.llmReason
