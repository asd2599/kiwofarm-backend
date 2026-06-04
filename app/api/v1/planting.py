"""심기(planting) 도메인 API — 작물 목록/상세 + 추천."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.planting import matrix
from app.core.planting.chat import answer as chat_answer
from app.core.planting.llm import attach_ai_explain
from app.core.planting.recommend import recommend
from app.schemas.planting import (
    CalendarAction,
    ChatRequest,
    ChatResponse,
    CropDetail,
    CropSummary,
    PlantingInput,
    PlantingRecommendResponse,
)

router = APIRouter(prefix="/planting", tags=["planting"])


def _summary(crop: dict) -> CropSummary:
    return CropSummary(
        id=crop["id"],
        name=crop["name"],
        category=crop["category"],
        difficulty=crop["difficulty"],
        environments=crop["environments"],
        sunlight=crop["sunlight"],
        min_sun_hours=crop["min_sun_hours"],
        days_to_harvest=crop["days_to_harvest"],
        water_need=crop["water_need"],
        container_ok=crop["container_ok"],
        source=crop["source"],
        needs_review=crop["needs_review"],
    )


@router.get("/crops", response_model=list[CropSummary])
async def list_crops() -> list[CropSummary]:
    """40종 작물 메타 목록."""
    return [_summary(c) for c in matrix.all_crops()]


@router.get("/crops/{crop_id}", response_model=CropDetail)
async def get_crop_detail(crop_id: str) -> CropDetail:
    """작물 상세 + 12개월 캘린더."""
    crop = matrix.get_crop(crop_id)
    if crop is None:
        raise HTTPException(status_code=404, detail=f"작물 없음: {crop_id}")
    calendar = {
        m: [
            CalendarAction(
                action=a.get("action", ""),
                method=a.get("method"),
                label=a.get("label"),
                plain=a.get("plain"),
            )
            for a in acts
        ]
        for m, acts in crop["calendar"].items()
    }
    return CropDetail(
        **_summary(crop).model_dump(),
        climate_note=crop["climate_note"],
        calendar=calendar,
    )


@router.post("/recommend", response_model=PlantingRecommendResponse)
async def post_recommend(payload: PlantingInput) -> PlantingRecommendResponse:
    """사용자 입력 → 결정적 스코어 추천(top N) + gpt-4o-mini 설명.

    추천 산정은 결정적 코드(재현성), AI 는 선택된 작물의 매트릭스 근거를 받아
    설명만 생성(키 없거나 실패 시 ai_explain 생략, 추천은 그대로 동작).
    """
    result = recommend(payload)
    items = await attach_ai_explain(result.recommendations, payload, result.month)
    return result.model_copy(update={"recommendations": items})


@router.post("/chat", response_model=ChatResponse)
async def post_chat(payload: ChatRequest) -> ChatResponse:
    """작목 상담 챗봇. 매트릭스(작물 카드) + RAG(garden 가중치 + _common) 근거로 답한다.

    경로 A: context 에 {user_input, recommendations} 를 캐리하면 추천 작물을 우선
    컨텍스트로 사용. 키 없거나 LLM 실패 시에도 안내문 + 칩을 200 으로 반환.
    """
    return await chat_answer(payload.messages, payload.context)
