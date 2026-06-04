from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.recommend import AreaUnit

TaskCategory = str  # seeding|growing|fertilize|water|pest|harvest|etc
TaskStatus = str  # planned|done|delayed


class FarmPlanCreate(BaseModel):
    """프론트 캘린더 설정 폼 입력."""

    startDate: date
    itemCode: str
    kindCode: str
    cropName: str
    region: str
    province: str | None = None
    area: float = Field(gt=0)
    areaUnit: AreaUnit
    # 방문 주기(주 1회 등)와 방문 요일(0=일~6=토). 주말농장 등 방문 기반 일정.
    visitFrequency: str | None = None
    visitDays: list[int] | None = None


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
    cropItemCode: str
    cropKindCode: str
    cropName: str
    region: str
    province: str | None = None
    area: float
    areaUnit: AreaUnit
    visitFrequency: str | None = None
    visitDays: list[int] | None = None
    trackProgress: bool
    tasks: list[FarmTaskOut]
    memos: list[TaskMemoOut]


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
    trackProgress: bool


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
