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


class TaskMemoOut(BaseModel):
    id: int
    memoDate: date
    content: str


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
