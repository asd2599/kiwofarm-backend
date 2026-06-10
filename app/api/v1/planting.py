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
    PlantingExplainResponse,
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
    """사용자 입력 → 결정적 스코어 추천(top N). 즉시 반환.

    AI 설명(gpt-4o-mini)은 응답을 지연시키지 않도록 분리했다. 프론트가 이 추천을
    먼저 렌더한 뒤 /recommend/explain 을 호출해 설명을 비동기로 채운다.
    """
    return recommend(payload)


@router.post("/recommend/explain", response_model=PlantingExplainResponse)
async def post_recommend_explain(payload: PlantingInput) -> PlantingExplainResponse:
    """동일 입력의 추천을 결정적으로 재현 → 작물별 AI 설명만 생성해 반환.

    추천 산정이 결정적이라 /recommend 와 같은 작물 집합이 나온다. 키 없거나 LLM
    실패 시 빈 맵(설명 없이 추천만 표시). 프론트는 도착하는 대로 카드에 채운다.
    """
    result = recommend(payload)
    items = await attach_ai_explain(result.recommendations, payload, result.month)
    explains = {it.crop_id: it.ai_explain for it in items if it.ai_explain is not None}
    return PlantingExplainResponse(explains=explains)


@router.post("/chat", response_model=ChatResponse)
async def post_chat(payload: ChatRequest) -> ChatResponse:
    """작목 상담 챗봇. 매트릭스(작물 카드) + RAG 근거로 답한다.

    질문/추천에서 특정 작물이 잡히면 그 작물 위주(+_common)로, 작물이 특정되지
    않으면 전 작물 임베딩에서 전역 검색해 우리가 가진 모든 작물 지식으로 답한다.
    키 없거나 LLM 실패 시에도 안내문 + 칩을 200 으로 반환.
    """
    return await chat_answer(payload.messages, payload.context)
