"""영농 캘린더 API (/api/v1/plans).

계획 생성(RAG+GPT) · 조회 · 진행추적 토글 · 작업 상태(완료/지연)+일정 재조정 · 날짜별 메모.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.farmplan.generator import _snap_to_visit_days, generate_plan
from app.db.models.farm_plan import FarmPlan, FarmTask, TaskMemo
from app.db.session import get_session
from app.schemas.farmplan import (
    FarmPlanCreate,
    FarmPlanOut,
    FarmTaskOut,
    MemoUpsert,
    SettingsUpdate,
    TaskMemoOut,
    TaskStatusUpdate,
)

router = APIRouter(prefix="/plans", tags=["plans"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_VALID_STATUS = {"planned", "done", "delayed"}


def _task_out(task: FarmTask, start: date) -> FarmTaskOut:
    d = start + timedelta(days=task.day_offset)
    return FarmTaskOut(
        id=task.id,
        title=task.title,
        detail=task.detail,
        category=task.category,
        dayOffset=task.day_offset,
        durationDays=task.duration_days,
        order=task.order,
        status=task.status,
        date=d,
        endDate=d + timedelta(days=max(0, task.duration_days - 1)),
        actualDate=task.actual_date,
        sourceNote=task.source_note,
    )


def _plan_out(plan: FarmPlan) -> FarmPlanOut:
    return FarmPlanOut(
        id=plan.id,
        startDate=plan.start_date,
        cropItemCode=plan.crop_item_code,
        cropKindCode=plan.crop_kind_code,
        cropName=plan.crop_name,
        region=plan.region,
        province=plan.province,
        area=plan.area,
        areaUnit=plan.area_unit,  # type: ignore[arg-type]
        visitFrequency=plan.visit_frequency,
        visitDays=plan.visit_days,
        trackProgress=plan.track_progress,
        tasks=[_task_out(t, plan.start_date) for t in plan.tasks],
        memos=[
            TaskMemoOut(id=m.id, memoDate=m.memo_date, content=m.content) for m in plan.memos
        ],
    )


async def _load_plan(session: AsyncSession, plan_id: int) -> FarmPlan:
    stmt = (
        select(FarmPlan)
        .where(FarmPlan.id == plan_id)
        .options(selectinload(FarmPlan.tasks), selectinload(FarmPlan.memos))
    )
    plan = await session.scalar(stmt)
    if plan is None:
        raise HTTPException(status_code=404, detail="해당 농사계획을 찾을 수 없습니다.")
    return plan


@router.post("", response_model=FarmPlanOut)
async def create_plan(
    payload: FarmPlanCreate, session: SessionDep
) -> FarmPlanOut:
    """시작일·작목·지역·면적으로 RAG 기반 농사계획 생성.

    첫 호출은 농사로 PDF 다운 → 임베딩 → RAG → GPT 로 길어진다(30~90초).
    """
    plan = await generate_plan(session, payload)
    plan = await _load_plan(session, plan.id)
    return _plan_out(plan)


@router.get("/{plan_id}", response_model=FarmPlanOut)
async def get_plan(
    plan_id: int, session: SessionDep
) -> FarmPlanOut:
    plan = await _load_plan(session, plan_id)
    return _plan_out(plan)


@router.patch("/{plan_id}/settings", response_model=FarmPlanOut)
async def update_settings(
    plan_id: int, payload: SettingsUpdate, session: SessionDep
) -> FarmPlanOut:
    """완료/지연 표시(진행 추적) on/off."""
    plan = await _load_plan(session, plan_id)
    plan.track_progress = payload.trackProgress
    await session.commit()
    plan = await _load_plan(session, plan_id)
    return _plan_out(plan)


@router.patch("/{plan_id}/tasks/{task_id}", response_model=FarmPlanOut)
async def update_task(
    plan_id: int,
    task_id: int,
    payload: TaskStatusUpdate,
    session: SessionDep,
) -> FarmPlanOut:
    """작업 완료/지연 표시. 진행추적 ON + 지연이면 이후 작업 일정을 자동 시프트.

    진행추적 OFF 면 상태 변경 자체를 막아(409) 정적 계획을 보존한다.
    """
    if payload.status not in _VALID_STATUS:
        raise HTTPException(status_code=422, detail="status 는 planned|done|delayed")

    plan = await _load_plan(session, plan_id)
    if not plan.track_progress:
        raise HTTPException(
            status_code=409, detail="진행 추적이 꺼져 있습니다. 먼저 settings 로 켜세요."
        )

    target = next((t for t in plan.tasks if t.id == task_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="해당 작업을 찾을 수 없습니다.")

    target.status = payload.status

    if payload.status == "delayed":
        delay = payload.delayDays or 0
        if delay > 0:
            # 대상 작업 자신 + 이후(order 큰) 작업의 day_offset 을 일괄 시프트
            affected = [t for t in plan.tasks if t.order >= target.order]
            for t in affected:
                t.day_offset += delay
            # 밀린 단기 작업을 방문 요일에 다시 맞춤(없으면 no-op) 후 전체 재정렬
            _snap_to_visit_days(affected, plan.start_date, plan.visit_days)
            plan.tasks.sort(key=lambda x: x.day_offset)
            for i, t in enumerate(plan.tasks):
                t.order = i
        target.actual_date = plan.start_date + timedelta(days=target.day_offset)
    elif payload.status == "done":
        target.actual_date = plan.start_date + timedelta(days=target.day_offset)
    else:  # planned 로 되돌림
        target.actual_date = None

    await session.commit()
    plan = await _load_plan(session, plan_id)
    return _plan_out(plan)


@router.put("/{plan_id}/memos", response_model=FarmPlanOut)
async def upsert_memo(
    plan_id: int, payload: MemoUpsert, session: SessionDep
) -> FarmPlanOut:
    """날짜별 메모 저장/수정. 내용이 비면 해당 날짜 메모 삭제."""
    plan = await _load_plan(session, plan_id)

    existing = next((m for m in plan.memos if m.memo_date == payload.memoDate), None)
    content = payload.content.strip()

    if not content:
        if existing is not None:
            await session.delete(existing)
    elif existing is not None:
        existing.content = content
    else:
        session.add(TaskMemo(plan_id=plan_id, memo_date=payload.memoDate, content=content))

    await session.commit()
    plan = await _load_plan(session, plan_id)
    return _plan_out(plan)


@router.delete("/{plan_id}/memos/{memo_date}", response_model=FarmPlanOut)
async def delete_memo(
    plan_id: int, memo_date: date, session: SessionDep
) -> FarmPlanOut:
    await _load_plan(session, plan_id)  # 존재 확인
    await session.execute(
        delete(TaskMemo).where(
            TaskMemo.plan_id == plan_id, TaskMemo.memo_date == memo_date
        )
    )
    await session.commit()
    plan = await _load_plan(session, plan_id)
    return _plan_out(plan)
