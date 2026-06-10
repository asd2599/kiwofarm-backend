from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.recommend import AreaUnit

TaskCategory = str  # seeding|growing|fertilize|water|pest|harvest|etc
TaskStatus = str  # planned|done|delayed


class GrowConditions(BaseModel):
    """추천받기(작목 추천) 위저드 입력 조건. 일정 생성 시 GPT가 함께 고려."""

    place: str | None = None  # 재배 장소 (베란다·옥상·노지·실내)
    sunHours: str | None = None  # 일조 시간 (<3h·3~5h·>5h)
    experience: str | None = None  # 영농 경험 (처음·1~2년·3년+)
    facility: list[str] | None = None  # 시설 (화분·플랜터·비닐터널·미니온실)
    direction: str | None = None  # 방향 (남향·동향·서향·북향)


class FarmPlanCreate(BaseModel):
    """프론트 캘린더 설정 폼 입력."""

    startDate: date
    name: str | None = None  # 텃밭 고유 이름(선택). 없으면 "{작물} 텃밭"으로 표시.
    itemCode: str
    kindCode: str
    cropName: str
    region: str
    province: str | None = None
    # 텃밭은 화분만 쓰는 경우가 많아 면적은 선택. 미입력이면 None(소규모로 처리).
    area: float | None = Field(default=None, gt=0)
    areaUnit: AreaUnit
    # 방문 주기(주 1회 등)와 방문 요일(0=일~6=토). 주말농장 등 방문 기반 일정.
    visitFrequency: str | None = None
    visitDays: list[int] | None = None
    # 추천받기에서 넘어온 재배 조건(선택). 생성 시 프롬프트에 반영.
    growConditions: GrowConditions | None = None


class FarmPlanBatchCreate(BaseModel):
    """여러 작물 계획을 한 번에 생성."""

    plans: list[FarmPlanCreate] = Field(min_length=1, max_length=20)


class FarmTaskOut(BaseModel):
    id: int
    title: str
    detail: str | None = None
    category: TaskCategory
    dayOffset: int
    durationDays: int
    order: int
    status: TaskStatus
    date: date  # start_date + day_offset (서버 계산)
    endDate: date  # date + (duration_days - 1)
    actualDate: date | None = None
    sourceNote: str | None = None


class MemoImageOut(BaseModel):
    id: int
    url: str  # 정적 서빙 URL (/uploads/...)
    originalName: str | None = None
    contentType: str | None = None
    size: int = 0


class TaskMemoOut(BaseModel):
    id: int
    memoDate: date
    content: str
    images: list[MemoImageOut] = []


class FarmPlanOut(BaseModel):
    id: int
    startDate: date
    name: str | None = None
    cropItemCode: str
    cropKindCode: str
    cropName: str
    cropSlug: str | None = None  # 도감 slug(40종) — 도감 딥링크/매칭용.
    region: str
    province: str | None = None
    area: float
    areaUnit: AreaUnit
    visitFrequency: str | None = None
    visitDays: list[int] | None = None
    trackProgress: bool
    # 검증된 수확 인증 기록이 하나라도 있으면 true → 캘린더에서 '완료'로 분류.
    harvested: bool = False
    tasks: list[FarmTaskOut]
    memos: list[TaskMemoOut]


class FarmPlanWithPointsOut(FarmPlanOut):
    """메모 저장·사진 업로드 응답 — 이번 저장으로 얻은 점수('+N점' 연출용)."""

    pointsEarned: int = 0
    pointsTotal: int = 0


class BatchFailure(BaseModel):
    index: int  # 입력 plans 배열에서의 위치
    cropName: str
    error: str


class FarmPlanBatchOut(BaseModel):
    created: list[FarmPlanOut]
    failed: list[BatchFailure]


class TaskStatusUpdate(BaseModel):
    status: TaskStatus  # planned|done|delayed
    delayDays: int | None = Field(default=None, ge=0)


class TaskDelayBatch(BaseModel):
    """같은 날짜의 여러 작업을 한 번에 같은 일수만큼 지연."""

    taskIds: list[int] = Field(min_length=1)
    delayDays: int = Field(ge=1)


class MemoUpsert(BaseModel):
    memoDate: date
    content: str


class SettingsUpdate(BaseModel):
    # 둘 다 선택 — 제공된 필드만 갱신(완료 추적 토글 / 텃밭 이름 변경에 공용).
    trackProgress: bool | None = None
    name: str | None = None


# ── 주간 다이제스트 (이번 주 할 일 3가지 + 코칭 한 줄) ───────────────────
class WeeklyTaskOut(BaseModel):
    id: int
    title: str
    category: TaskCategory
    date: date
    status: TaskStatus
    message: str  # 그 작업 맞춤 코칭 멘트 한 문장(알림 본문)


class WeeklyDigestOut(BaseModel):
    weekStart: date  # 월요일
    weekEnd: date  # 일요일
    tasks: list[WeeklyTaskOut]  # 이번 주 작업 전체(작업별 멘트 포함)


# ── 위기 알림 (병해충 발생정보 + 기상 특보) ───────────────────────────
class CrisisAlertOut(BaseModel):
    type: str  # pest | weather
    severity: str  # info | warn | danger
    title: str
    detail: str
    source: str
    link: str | None = None
    date: str | None = None


class AlertsOut(BaseModel):
    alerts: list[CrisisAlertOut]


# ── 멀티 작물 통합 캘린더 ──────────────────────────────────────────────
class FarmPlanSummary(BaseModel):
    """통합 캘린더에서 작물(plan)을 선택하기 위한 요약."""

    id: int
    name: str | None = None
    cropName: str
    cropItemCode: str
    cropKindCode: str
    startDate: date
    region: str
    province: str | None = None
    area: float
    areaUnit: AreaUnit
    trackProgress: bool
    taskCount: int


class CalendarTaskOut(FarmTaskOut):
    """작업 + 어떤 작물(plan) 것인지 식별 정보."""

    planId: int
    cropName: str


class CalendarMemoOut(TaskMemoOut):
    """메모 + 어떤 작물(plan) 것인지 식별 정보."""

    planId: int
    cropName: str


class CalendarOut(BaseModel):
    plans: list[FarmPlanSummary]  # 이번 조회에 포함된 작물 목록
    tasks: list[CalendarTaskOut]
    memos: list[CalendarMemoOut]
