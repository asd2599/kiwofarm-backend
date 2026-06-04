"""심기(planting) 도메인 스키마 — 입력/추천/작물 (Task 부록 C)."""

from __future__ import annotations

from pydantic import BaseModel, Field

# ───────────────────────── 입력 ─────────────────────────

Place = str  # 베란다 | 옥상 | 노지 | 실내
SunHours = str  # <3h | 3~5h | >5h
Experience = str  # 처음 | 1~2년 | 3년+
StartWhen = str  # now | next_month


class PlantingInput(BaseModel):
    """추천 요청(필수 + 선택). 부록 C."""

    sigungu: str = Field(..., description="시군구 (예: '경기도 성남시')")
    place: Place = Field(..., description="베란다 | 옥상 | 노지 | 실내")
    sun_hours: SunHours = Field(..., description="<3h | 3~5h | >5h")
    experience: Experience = Field("처음", description="처음 | 1~2년 | 3년+")
    # 선택
    direction: str | None = Field(None, description="남향 | 동향 | 서향 | 북향")
    area_m2: float | None = Field(None, description="면적(㎡)")
    frequency: str | None = Field(None, description="매일 | 주2~3회 | 주말만")
    start: StartWhen = Field("now", description="now | next_month")
    facility: list[str] = Field(
        default_factory=list, description="화분 | 플랜터 | 비닐터널 | 미니온실"
    )
    prefs: list[str] = Field(
        default_factory=list, description="잎채소 | 열매채소 | 뿌리채소 | 허브"
    )
    top_n: int = Field(6, ge=1, le=20)


# ───────────────────────── 작물/캘린더 ─────────────────────────


class CalendarAction(BaseModel):
    action: str  # 파종 | 정식 | 관리 | 수확
    method: str | None = None  # 작형(예: 가을재배, 촉성재배)
    label: str | None = None  # 원문 작업명
    plain: str | None = None  # 초보용 한 줄 설명


class CropSummary(BaseModel):
    id: str
    name: str
    category: str
    difficulty: int
    environments: list[str]
    sunlight: str
    min_sun_hours: int
    days_to_harvest: list[int]
    water_need: str
    container_ok: bool
    source: str
    needs_review: bool = False


class CropDetail(CropSummary):
    climate_note: str | None = None
    calendar: dict[str, list[CalendarAction]]  # "1".."12" → 행동들


# ───────────────────────── 추천 응답 ─────────────────────────


class AiExplain(BaseModel):
    reason: str = ""
    tips: list[str] = Field(default_factory=list)
    first_month_todo: list[str] = Field(default_factory=list)


class RecommendationItem(BaseModel):
    crop_id: str
    name: str
    category: str
    difficulty: int
    score: int
    reasons: list[str]
    plantable_now: bool
    plantable_next: bool
    calendar_this_month: list[CalendarAction]
    days_to_harvest: list[int]
    source: str
    needs_review: bool = False
    ai_explain: AiExplain | None = None


class PlantingRecommendResponse(BaseModel):
    month: int
    zone: str
    recommendations: list[RecommendationItem]
    next_month_candidates: list[str]


# ───────────────────────── 챗봇 (§5, 부록 D/E) ─────────────────────────


class ChatMessage(BaseModel):
    role: str  # user | assistant
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., description="대화 이력(최소 1개, 마지막은 user)")
    # 경로 A: 추천 결과 화면에서 캐리하는 컨텍스트(user_input + recommendations)
    context: dict | None = Field(None, description="{user_input, recommendations}")


class ChatSource(BaseModel):
    crop_id: str
    name: str


class ChatResponse(BaseModel):
    answer: str
    chips: list[str]  # 다음 추천 칩(부록 E)
    sources: list[ChatSource]  # 답변 근거 작물
