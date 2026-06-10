"""영농 캘린더 API (/api/v1/plans).

계획 생성(RAG+GPT) · 조회 · 진행추적 토글 · 작업 상태(완료/지연)+일정 재조정 · 날짜별 메모.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, undefer

from app.api.deps import DeviceDep
from app.core.farmplan.alerts import build_alerts
from app.core.farmplan.coach import weekly_task_messages
from app.core.farmplan.generator import _snap_to_visit_days, generate_plan
from app.core.rewards.points import total_points
from app.core.storage import delete_file, file_url, read_image
from app.core.harvest import rules
from app.db.models.farm_plan import FarmPlan, FarmTask, MemoImage, TaskMemo
from app.db.models.harvest import HarvestRecord
from app.db.session import async_session_factory, get_session
from app.schemas.farmplan import (
    AlertsOut,
    BatchFailure,
    CalendarMemoOut,
    CalendarOut,
    CalendarTaskOut,
    CrisisAlertOut,
    FarmPlanBatchCreate,
    FarmPlanBatchOut,
    FarmPlanCreate,
    FarmPlanOut,
    FarmPlanSummary,
    FarmPlanWithPointsOut,
    FarmTaskOut,
    MemoImageOut,
    MemoUpsert,
    SettingsUpdate,
    TaskDelayBatch,
    TaskMemoOut,
    TaskStatusUpdate,
    WeeklyDigestOut,
    WeeklyTaskOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["plans"])

# 배치 생성 동시 처리 상한(OpenAI/RAG 부하 보호). 작물마다 별도 세션을 쓴다.
_BATCH_CONCURRENCY = 4

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


def _image_url(img: MemoImage) -> str:
    """사진 URL — DB(bytea) 저장분은 서빙 API, 디스크 레거시는 /uploads 정적 경로."""
    if img.file_path:
        return file_url(img.file_path)
    return f"/api/v1/plans/memo-images/{img.id}"


def _memo_out(memo: TaskMemo) -> TaskMemoOut:
    return TaskMemoOut(
        id=memo.id,
        memoDate=memo.memo_date,
        content=memo.content,
        images=[
            MemoImageOut(
                id=img.id,
                url=_image_url(img),
                originalName=img.original_name,
                contentType=img.content_type,
                size=img.size_bytes,
            )
            for img in memo.images
        ],
    )


def _plan_out(plan: FarmPlan) -> FarmPlanOut:
    return FarmPlanOut(
        id=plan.id,
        startDate=plan.start_date,
        name=plan.name,
        cropItemCode=plan.crop_item_code,
        cropKindCode=plan.crop_kind_code,
        cropName=plan.crop_name,
        # 도감 slug(40종) — 딥링크/도감 매칭용. 카탈로그 밖 작물이면 None.
        cropSlug=rules.plan_slug(plan),
        region=plan.region,
        province=plan.province,
        area=plan.area,
        areaUnit=plan.area_unit,  # type: ignore[arg-type]
        visitFrequency=plan.visit_frequency,
        visitDays=plan.visit_days,
        trackProgress=plan.track_progress,
        harvested=any(r.verified for r in plan.harvest_records),
        tasks=[_task_out(t, plan.start_date) for t in plan.tasks],
        memos=[_memo_out(m) for m in plan.memos],
    )


def _plan_summary(plan: FarmPlan) -> FarmPlanSummary:
    return FarmPlanSummary(
        id=plan.id,
        name=plan.name,
        cropName=plan.crop_name,
        cropItemCode=plan.crop_item_code,
        cropKindCode=plan.crop_kind_code,
        startDate=plan.start_date,
        region=plan.region,
        province=plan.province,
        area=plan.area,
        areaUnit=plan.area_unit,  # type: ignore[arg-type]
        trackProgress=plan.track_progress,
        taskCount=len(plan.tasks),
    )


def _delete_memo_files(memo: TaskMemo) -> None:
    """디스크 레거시 사진 파일 제거(DB 행·bytea 는 CASCADE 가 처리)."""
    for img in memo.images:
        if img.file_path:
            delete_file(img.file_path)


async def _load_plan(session: AsyncSession, plan_id: int, device_id: str) -> FarmPlan:
    stmt = (
        select(FarmPlan)
        .where(FarmPlan.id == plan_id, FarmPlan.device_id == device_id)
        .options(
            selectinload(FarmPlan.tasks),
            selectinload(FarmPlan.memos).selectinload(TaskMemo.images),
            selectinload(FarmPlan.harvest_records),
        )
        # 같은 세션에서 add 후 재조회 시, 이미 로드된 인스턴스의 컬렉션을
        # 새 쿼리 결과로 덮어쓴다(신규 메모/사진이 응답에 빠지는 문제 방지).
        .execution_options(populate_existing=True)
    )
    plan = await session.scalar(stmt)
    if plan is None:
        raise HTTPException(status_code=404, detail="해당 농사계획을 찾을 수 없습니다.")
    return plan


@router.post("", response_model=FarmPlanOut)
async def create_plan(
    payload: FarmPlanCreate, session: SessionDep, device: DeviceDep
) -> FarmPlanOut:
    """시작일·작목·지역·면적으로 RAG 기반 농사계획 생성.

    첫 호출은 농사로 PDF 다운 → 임베딩 → RAG → GPT 로 길어진다(30~90초).
    """
    plan = await generate_plan(session, payload, device)
    plan = await _load_plan(session, plan.id, device)
    return _plan_out(plan)


@router.post("/batch", response_model=FarmPlanBatchOut)
async def create_plans_batch(
    payload: FarmPlanBatchCreate, session: SessionDep, device: DeviceDep
) -> FarmPlanBatchOut:
    """여러 작물 계획을 한 번에 생성(작물당 RAG+GPT, 최대 4개 동시 처리).

    작물마다 독립 세션으로 생성하므로 한 작물이 실패해도 나머지는 계속 만들고,
    실패는 failed 로 보고한다. created 는 입력 순서를 따른다.
    """
    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def _one(idx: int, p: FarmPlanCreate) -> tuple[int, int | None, str | None]:
        async with sem:
            try:
                # 동시 실행 안전을 위해 작물마다 별도 세션 사용(AsyncSession 은 공유 불가).
                async with async_session_factory() as s:
                    plan = await generate_plan(s, p, device)
                    return idx, plan.id, None
            except Exception as e:  # noqa: BLE001 - 개별 실패가 전체 배치를 막지 않도록
                log.exception("배치 계획 생성 실패 idx=%s crop=%s", idx, p.cropName)
                return idx, None, str(e)

    results = await asyncio.gather(
        *[_one(i, p) for i, p in enumerate(payload.plans)]
    )

    created: list[FarmPlanOut] = []
    failed: list[BatchFailure] = []
    for idx, plan_id, err in sorted(results):
        if plan_id is not None:
            plan = await _load_plan(session, plan_id, device)
            created.append(_plan_out(plan))
        else:
            failed.append(
                BatchFailure(
                    index=idx, cropName=payload.plans[idx].cropName, error=err or "unknown"
                )
            )
    return FarmPlanBatchOut(created=created, failed=failed)


@router.get("", response_model=list[FarmPlanSummary])
async def list_plans(session: SessionDep, device: DeviceDep) -> list[FarmPlanSummary]:
    """내(디바이스) 농사계획 요약 목록. 통합 캘린더에서 볼 작물을 고르는 데 쓴다."""
    stmt = (
        select(FarmPlan)
        .where(FarmPlan.device_id == device)
        .options(selectinload(FarmPlan.tasks))
        .order_by(FarmPlan.start_date)
    )
    plans = (await session.scalars(stmt)).all()
    return [_plan_summary(p) for p in plans]


@router.get("/calendar", response_model=CalendarOut)
async def calendar(
    session: SessionDep, device: DeviceDep,
    planIds: Annotated[
        str | None,
        Query(description="콤마 구분 plan ID(예: 1,2,3). 미지정 시 전체 작물."),
    ] = None,
    from_: Annotated[date | None, Query(alias="from", description="조회 시작일(포함)")] = None,
    to: Annotated[date | None, Query(description="조회 종료일(포함)")] = None,
    includeMemos: Annotated[bool, Query(description="메모 포함 여부")] = True,
) -> CalendarOut:
    """ 여러 작물을 하나의 캘린더로 통합 조회. (개별 선택 가능) """
    ids: list[int] | None = None
    if planIds:
        try:
            ids = [int(x) for x in planIds.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(
                status_code=422, detail="planIds 는 콤마로 구분된 정수여야 합니다."
            ) from None

    stmt = (
        select(FarmPlan)
        .where(FarmPlan.device_id == device)
        .options(
            selectinload(FarmPlan.tasks),
            selectinload(FarmPlan.memos).selectinload(TaskMemo.images),
        )
    )
    if ids:
        stmt = stmt.where(FarmPlan.id.in_(ids))
    stmt = stmt.order_by(FarmPlan.start_date)
    plans = (await session.scalars(stmt)).all()

    tasks: list[CalendarTaskOut] = []
    memos: list[CalendarMemoOut] = []
    for p in plans:
        for t in p.tasks:
            base = _task_out(t, p.start_date)
            # [date, endDate] 가 [from, to] 와 겹치는 작업만 포함.
            if from_ is not None and base.endDate < from_:
                continue
            if to is not None and base.date > to:
                continue
            tasks.append(
                CalendarTaskOut(**base.model_dump(), planId=p.id, cropName=p.crop_name)
            )
        if includeMemos:
            for m in p.memos:
                if from_ is not None and m.memo_date < from_:
                    continue
                if to is not None and m.memo_date > to:
                    continue
                memos.append(
                    CalendarMemoOut(
                        **_memo_out(m).model_dump(), planId=p.id, cropName=p.crop_name
                    )
                )

    tasks.sort(key=lambda x: (x.date, x.order))
    memos.sort(key=lambda x: x.memoDate)
    return CalendarOut(
        plans=[_plan_summary(p) for p in plans],
        tasks=tasks,
        memos=memos,
    )


@router.get("/memo-images/{image_id}")
async def get_memo_image(image_id: int, session: SessionDep) -> Response:
    """메모 사진 바이트 서빙(DB bytea 저장분). 사진은 불변이라 장기 캐시.

    <img src> 요청은 X-Device-Id 헤더를 못 보내므로 디바이스 검사 없음.
    """
    img = await session.scalar(
        select(MemoImage)
        .where(MemoImage.id == image_id)
        .options(undefer(MemoImage.data))
    )
    if img is None or img.data is None:
        raise HTTPException(status_code=404, detail="해당 사진을 찾을 수 없습니다.")
    return Response(
        content=img.data,
        media_type=img.content_type or "image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/{plan_id}", response_model=FarmPlanOut)
async def get_plan(
    plan_id: int, session: SessionDep, device: DeviceDep
) -> FarmPlanOut:
    plan = await _load_plan(session, plan_id, device)
    return _plan_out(plan)


@router.get("/{plan_id}/weekly", response_model=WeeklyDigestOut)
async def weekly_digest(
    plan_id: int,
    session: SessionDep, device: DeviceDep,
    ref: Annotated[
        date | None,
        Query(alias="date", description="기준 날짜(그 주). 미지정 시 오늘."),
    ] = None,
) -> WeeklyDigestOut:
    """이번 주(월~일) 작업 전체 + 작업별 코칭 멘트.

    ref 가 속한 주(월~일)와 기간이 겹치는 작업을 날짜순으로 모으고(완료 포함), 각 작업에
    그 작물 맞춤 멘트(알림 본문)를 LLM 으로 붙인다. 여러날에 걸친 작업은 시작 주가 아니어도
    기간이 겹치는 모든 주에 포함된다.
    """
    plan = await _load_plan(session, plan_id, device)
    base = ref or date.today()
    monday = base - timedelta(days=base.weekday())  # weekday: 월=0
    sunday = monday + timedelta(days=6)

    in_week: list[tuple[date, FarmTask]] = []
    for t in plan.tasks:
        start = plan.start_date + timedelta(days=t.day_offset)
        end = start + timedelta(days=max(0, t.duration_days - 1))
        if start <= sunday and end >= monday:  # 기간이 이 주와 겹침
            in_week.append((start, t))
    in_week.sort(key=lambda x: (x[0], x[1].order))

    messages = await weekly_task_messages(
        plan.crop_name, plan.region, [t.title for _, t in in_week]
    )
    return WeeklyDigestOut(
        weekStart=monday,
        weekEnd=sunday,
        tasks=[
            WeeklyTaskOut(
                id=t.id,
                title=t.title,
                category=t.category,
                date=d,
                status=t.status,
                message=messages[i],
            )
            for i, (d, t) in enumerate(in_week)
        ],
    )


@router.get("/{plan_id}/alerts", response_model=AlertsOut)
async def plan_alerts(
    plan_id: int,
    session: SessionDep, device: DeviceDep,
    ref: Annotated[
        date | None, Query(alias="date", description="기준 날짜. 미지정 시 오늘.")
    ] = None,
) -> AlertsOut:
    """위기 알림 — 병해충 발생정보(+ 향후 기상특보). 작물 계획의 지역 기준."""
    plan = await _load_plan(session, plan_id, device)
    alerts = await build_alerts(
        plan.crop_name, plan.region, plan.province, ref or date.today()
    )
    return AlertsOut(alerts=[CrisisAlertOut(**vars(a)) for a in alerts])


@router.patch("/{plan_id}/settings", response_model=FarmPlanOut)
async def update_settings(
    plan_id: int, payload: SettingsUpdate, session: SessionDep, device: DeviceDep
) -> FarmPlanOut:
    """계획 설정 — 완료/지연 표시(진행 추적) on/off + 텃밭 이름 변경. 제공된 필드만 갱신."""
    plan = await _load_plan(session, plan_id, device)
    if payload.trackProgress is not None:
        plan.track_progress = payload.trackProgress
    if payload.name is not None:
        plan.name = payload.name.strip()[:255] or None
    await session.commit()
    plan = await _load_plan(session, plan_id, device)
    return _plan_out(plan)


@router.patch("/{plan_id}/tasks/delay-batch", response_model=FarmPlanOut)
async def delay_tasks_batch(
    plan_id: int, payload: TaskDelayBatch, session: SessionDep, device: DeviceDep
) -> FarmPlanOut:
    """같은 날짜의 여러 작업을 한 번에 같은 일수만큼 지연.

    선택한 작업들(보통 같은 날짜)은 입력한 일수만큼 그대로 이동하고,
    그 이후(order 가 더 큰) 작업만 방문 요일에 맞춰 스냅한다.
    """
    plan = await _load_plan(session, plan_id, device)
    target_ids = set(payload.taskIds)
    targets = [t for t in plan.tasks if t.id in target_ids]
    if not targets:
        raise HTTPException(status_code=404, detail="해당 작업을 찾을 수 없습니다.")

    delay = payload.delayDays
    # 선택 작업 중 가장 앞선 것부터 그 이후 작업을 일괄 시프트
    min_order = min(t.order for t in targets)
    affected = [t for t in plan.tasks if t.order >= min_order]
    for t in affected:
        t.day_offset += delay
    # 입력 대상 작업들은 입력한 만큼만 그대로, 나머지(이후) 단기 작업만 방문 요일 스냅
    others = [t for t in affected if t.id not in target_ids]
    _snap_to_visit_days(others, plan.start_date, plan.visit_days)
    plan.tasks.sort(key=lambda x: x.day_offset)
    for i, t in enumerate(plan.tasks):
        t.order = i

    for t in targets:
        t.status = "delayed"
        t.actual_date = plan.start_date + timedelta(days=t.day_offset)

    await session.commit()
    plan = await _load_plan(session, plan_id, device)
    return _plan_out(plan)


@router.patch("/{plan_id}/tasks/{task_id}", response_model=FarmPlanOut)
async def update_task(
    plan_id: int,
    task_id: int,
    payload: TaskStatusUpdate,
    session: SessionDep, device: DeviceDep,
) -> FarmPlanOut:
    """작업 완료(done)/지연(delayed)/되돌리기(planned) 표시.

    지연이면 대상 작업 + 이후 작업의 일정을 delayDays 만큼 자동 시프트한다.
    (track_progress 값과 무관하게 상태 변경을 허용한다 — 캘린더 카드의 완료/지연 버튼.)
    """
    if payload.status not in _VALID_STATUS:
        raise HTTPException(status_code=422, detail="status 는 planned|done|delayed")

    plan = await _load_plan(session, plan_id, device)
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
            # 시프트를 입력한 대상 작업은 입력한 만큼만 그대로 이동.
            # 방문 요일 스냅은 나머지(이후) 단기 작업에만 적용한다(없으면 no-op) 후 전체 재정렬.
            others = [t for t in affected if t is not target]
            _snap_to_visit_days(others, plan.start_date, plan.visit_days)
            plan.tasks.sort(key=lambda x: x.day_offset)
            for i, t in enumerate(plan.tasks):
                t.order = i
        target.actual_date = plan.start_date + timedelta(days=target.day_offset)
    elif payload.status == "done":
        target.actual_date = plan.start_date + timedelta(days=target.day_offset)
    else:  # planned 로 되돌림
        target.actual_date = None

    await session.commit()
    plan = await _load_plan(session, plan_id, device)
    return _plan_out(plan)


@router.put("/{plan_id}/memos", response_model=FarmPlanWithPointsOut)
async def upsert_memo(
    plan_id: int, payload: MemoUpsert, session: SessionDep, device: DeviceDep
) -> FarmPlanWithPointsOut:
    """날짜별 메모 저장/수정. 내용이 비면 해당 날짜 메모 삭제. 새 기록은 점수 적립."""
    plan = await _load_plan(session, plan_id, device)
    points_before = await total_points(session, device)

    existing = next((m for m in plan.memos if m.memo_date == payload.memoDate), None)
    content = payload.content.strip()

    # 텍스트가 비어도 사진이 있으면 메모를 유지한다(사진만 있는 메모 허용).
    if not content and (existing is None or not existing.images):
        if existing is not None:
            _delete_memo_files(existing)
            await session.delete(existing)
    elif existing is not None:
        existing.content = content
    else:
        session.add(TaskMemo(plan_id=plan_id, memo_date=payload.memoDate, content=content))

    await session.commit()
    points_total = await total_points(session, device)
    plan = await _load_plan(session, plan_id, device)
    return FarmPlanWithPointsOut(
        **_plan_out(plan).model_dump(),
        pointsEarned=max(0, points_total - points_before),
        pointsTotal=points_total,
    )


@router.post("/{plan_id}/memos/{memo_date}/images", response_model=FarmPlanWithPointsOut)
async def upload_memo_images(
    plan_id: int,
    memo_date: date,
    session: SessionDep, device: DeviceDep,
    files: Annotated[list[UploadFile], File(description="첨부할 이미지(여러 장 가능)")],
) -> FarmPlanWithPointsOut:
    """해당 날짜 메모에 사진 첨부(여러 장 가능). 메모가 없으면 자동 생성. 점수 적립."""
    plan = await _load_plan(session, plan_id, device)
    points_before = await total_points(session, device)

    memo = next((m for m in plan.memos if m.memo_date == memo_date), None)
    if memo is None:
        memo = TaskMemo(plan_id=plan_id, memo_date=memo_date, content="")
        session.add(memo)
        await session.flush()  # memo.id 확보

    for f in files:
        data = await read_image(f)  # 형식·크기 검증 후 바이트 — DB(bytea)에 저장
        session.add(
            MemoImage(
                memo_id=memo.id,
                data=data,
                original_name=f.filename,
                content_type=f.content_type,
                size_bytes=len(data),
            )
        )

    await session.commit()
    points_total = await total_points(session, device)
    plan = await _load_plan(session, plan_id, device)
    return FarmPlanWithPointsOut(
        **_plan_out(plan).model_dump(),
        pointsEarned=max(0, points_total - points_before),
        pointsTotal=points_total,
    )


@router.delete("/{plan_id}/memos/images/{image_id}", response_model=FarmPlanOut)
async def delete_memo_image(
    plan_id: int, image_id: int, session: SessionDep, device: DeviceDep
) -> FarmPlanOut:
    """메모 사진 1장 삭제(파일 + DB 행). 사진 삭제로 메모가 비어도 메모는 유지한다."""
    plan = await _load_plan(session, plan_id, device)

    target = next(
        (img for m in plan.memos for img in m.images if img.id == image_id), None
    )
    if target is None:
        raise HTTPException(status_code=404, detail="해당 사진을 찾을 수 없습니다.")

    if target.file_path:
        delete_file(target.file_path)
    await session.delete(target)
    await session.commit()
    plan = await _load_plan(session, plan_id, device)
    return _plan_out(plan)


@router.delete("/{plan_id}/memos/{memo_date}", response_model=FarmPlanOut)
async def delete_memo(
    plan_id: int, memo_date: date, session: SessionDep, device: DeviceDep
) -> FarmPlanOut:
    plan = await _load_plan(session, plan_id, device)
    existing = next((m for m in plan.memos if m.memo_date == memo_date), None)
    if existing is not None:
        _delete_memo_files(existing)  # 디스크 파일 정리(DB 행은 CASCADE)
        await session.delete(existing)
        await session.commit()
        plan = await _load_plan(session, plan_id, device)
    return _plan_out(plan)


@router.delete("/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: int, session: SessionDep, device: DeviceDep
) -> Response:
    """농사 계획 삭제. 작업·메모·사진은 cascade, 디스크 메모 사진은 직접 정리."""
    plan = await _load_plan(session, plan_id, device)
    for m in plan.memos:
        _delete_memo_files(m)
    await session.delete(plan)
    await session.commit()
    return Response(status_code=204)
