"""RAG 기반 영농 캘린더(농사계획) 생성.

흐름:
  1. ensure_crop_ingested 로 작목 청크 확보 (농사로 PDF → 임베딩, 실패 시 GPT general).
  2. facet 별 RAG 질의로 관련 청크 회수 → 컨텍스트 합성.
  3. GPT(json) 로 시작일 기준 상대 오프셋(day_offset) task 리스트 생성.
  4. GPT/RAG 실패 시 표준 재배 단계 기반 결정론적 fallback.
  5. FarmPlan + FarmTask 영속화.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rag import knowledge
from app.core.rag import retrieve as rag_retrieve
from app.core.rag.ingest import crop_key, ensure_crop_ingested
from app.db.models.farm_plan import FarmPlan, FarmTask
from app.schemas.farmplan import FarmPlanCreate

log = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

# 계획 facet → RAG 질의문. 병해충 예방을 명시적 facet 으로 포함.
_FACETS: list[tuple[str, str]] = [
    ("파종/육묘/정식 시기", "파종 육묘 정식 시기와 방법"),
    ("시비", "밑거름 웃거름 시비 시기와 방법"),
    ("물·관수 관리", "물주기 관수 토양 수분 관리"),
    ("병해충 예방", "병해충 예방 시기별 방제 주요 병해충 주의사항"),
    ("수확·저장", "수확 시기 방법 수확 후 저장"),
    ("생육 단계별 작업", "월별 생육 단계별 주요 농작업"),
]

_VALID_CATEGORIES = {
    "seeding",
    "growing",
    "fertilize",
    "water",
    "pest",
    "harvest",
    "etc",
}


async def _gather_context(ckey: str) -> str:
    """facet 별 청크를 회수해 라벨링된 컨텍스트 블록으로 합성."""
    blocks: list[str] = []
    for label, query in _FACETS:
        try:
            chunks = await rag_retrieve.retrieve(ckey, query, k=4)
        except Exception as e:  # noqa: BLE001
            log.info("retrieve 실패 facet=%s reason=%s", label, e)
            chunks = []
        if chunks:
            joined = "\n".join(f"- {c}" for c in chunks)
            blocks.append(f"## {label}\n{joined}")
    return "\n\n".join(blocks)


def _build_prompt(payload: FarmPlanCreate, context: str) -> str:
    region = f"{payload.province or ''} {payload.region}".strip()
    return (
        f"작목: {payload.cropName}\n"
        f"재배 시작일: {payload.startDate.isoformat()}\n"
        f"지역: {region}\n"
        f"농지 면적: {payload.area} {payload.areaUnit}\n\n"
        f"--- 농업기술 참고자료 (농사로 기반) ---\n{context or '(참고자료 없음)'}\n--- 끝 ---\n\n"
        "위 자료를 바탕으로 시작일부터 한 작기(보통 1년 이내)의 농사 일정을 만드세요. "
        "각 작업은 시작일로부터의 day_offset(0=시작일 당일)과 "
        "duration_days(작업 지속 일수)로 표현합니다. "
        "지역 기후와 면적을 고려해 현실적인 시기를 잡고, 병해충 예방 작업을 반드시 포함하세요. "
        "출력은 JSON 객체 하나만. 형식: "
        '{"tasks": [{"title": "작업명(30자 이내)", "detail": "구체 방법 80자 이내", '
        '"category": "seeding|growing|fertilize|water|pest|harvest|etc", '
        '"day_offset": 정수, "duration_days": 정수, "source_note": "근거 한 줄"}]}. '
        "작업은 시간순으로 8~16개. day_offset 오름차순 정렬. "
        "본문에 없는 시기는 표준 재배력으로 합리적으로 추정."
    )


async def _gpt_tasks(payload: FarmPlanCreate, context: str) -> list[dict]:
    if not settings.openai_api_key:
        return []
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            response_format={"type": "json_object"},
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "당신은 한국 농업 영농계획 전문가입니다. "
                        "실행 가능한 날짜별 농작업 일정을 설계합니다."
                    ),
                },
                {"role": "user", "content": _build_prompt(payload, context)},
            ],
        )
    except Exception as e:  # noqa: BLE001
        log.info("계획 GPT 호출 실패: %s", e)
        return []
    content = resp.choices[0].message.content or ""
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        return []
    raw = obj.get("tasks") if isinstance(obj, dict) else None
    return raw if isinstance(raw, list) else []


# 생육관리로 분류됐지만 실제로는 물 관리인 작업을 물·관수(water)로 넘기기 위한 키워드.
# '물' 단독은 '식물' 등 오탐 위험이 있어 구체 표현만 사용.
_WATER_KEYWORDS = (
    "관수",
    "물주",
    "물 주",
    "물대기",
    "물 대기",
    "급수",
    "수분",
    "물 공급",
    "물공급",
)

# 수확·저장 카테고리로 들어가야 하는데 GPT 가 etc/growing 등으로 잘못 분류한 경우
# 재분류하기 위한 키워드. '저장' 단독은 '토양 저장' 등 오탐 가능성 낮아 사용.
_HARVEST_KEYWORDS = (
    "수확",
    "수확 후",
    "수확후",
    "저장",
    "저온 저장",
    "저온저장",
    "저장 출하",
    "저장출하",
    "후숙",
    "큐어링",
)


def _normalize_tasks(raw: list[dict]) -> list[FarmTask]:
    tasks: list[FarmTask] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        detail = str(item.get("detail") or "").strip() or None
        category = str(item.get("category") or "etc").strip()
        if category not in _VALID_CATEGORIES:
            category = "etc"
        # 생육관리인데 물 내용이면 물·관수로 재분류
        if category == "growing" and any(
            k in f"{title} {detail or ''}" for k in _WATER_KEYWORDS
        ):
            category = "water"
        # 수확/저장 키워드인데 etc 또는 growing 으로 잘못 분류된 경우 harvest 로 재분류
        if category in ("etc", "growing") and any(
            k in f"{title} {detail or ''}" for k in _HARVEST_KEYWORDS
        ):
            category = "harvest"
        try:
            day_offset = max(0, int(item.get("day_offset", 0)))
        except (TypeError, ValueError):
            day_offset = 0
        try:
            duration = max(1, int(item.get("duration_days", 1)))
        except (TypeError, ValueError):
            duration = 1
        tasks.append(
            FarmTask(
                title=title[:255],
                detail=detail,
                category=category,
                day_offset=day_offset,
                duration_days=duration,
                source_note=(str(item.get("source_note") or "").strip() or None),
            )
        )
    tasks.sort(key=lambda t: t.day_offset)
    for i, t in enumerate(tasks):
        t.order = i
    return tasks


# 표준 재배 단계 fallback (GPT/RAG 전부 실패 시). 작목 무관 골격.
_FALLBACK: list[tuple[str, str, int, int]] = [
    ("정식 준비 · 토양 정비", "fertilize", 0, 7),
    ("파종 / 정식", "seeding", 7, 5),
    ("초기 관수 · 활착 관리", "water", 14, 14),
    ("1차 웃거름", "fertilize", 30, 2),
    ("병해충 예찰 · 예방 방제", "pest", 45, 3),
    ("생육 관리 · 정지/유인", "growing", 60, 21),
    ("2차 웃거름", "fertilize", 75, 2),
    ("병해충 정기 방제", "pest", 90, 3),
    ("수확 시작", "harvest", 110, 14),
    ("수확 마무리 · 저장", "harvest", 130, 7),
]


def _fallback_tasks() -> list[FarmTask]:
    tasks = [
        FarmTask(
            title=title,
            detail=None,
            category=cat,
            day_offset=off,
            duration_days=dur,
            order=i,
            source_note="표준 재배력 기반 기본 일정",
        )
        for i, (title, cat, off, dur) in enumerate(_FALLBACK)
    ]
    return tasks


def _snap_to_visit_days(
    tasks: list[FarmTask], start: date, visit_days: list[int] | None
) -> None:
    """단기(하루) 작업을 방문 요일로 앞당겨 스냅한다(다음 방문일에 수행).

    visit_days: 0=일~6=토 정수 리스트. 기간형(duration>1) 작업과 빈 리스트는 그대로 둔다.
    스냅 후 day_offset 기준 재정렬·order 재부여는 호출부에서 수행.
    """
    allowed = {d % 7 for d in (visit_days or []) if isinstance(d, int)}
    if not allowed:
        return
    for t in tasks:
        if t.duration_days and t.duration_days > 1:
            continue
        base = start + timedelta(days=t.day_offset)
        for step in range(8):  # 같은 날 포함 최대 7일 내 다음 방문 요일
            cand = base + timedelta(days=step)
            if cand.isoweekday() % 7 in allowed:  # isoweekday%7: 일=0~토=6
                t.day_offset = (cand - start).days
                break


async def generate_plan(session: AsyncSession, payload: FarmPlanCreate) -> FarmPlan:
    """RAG+GPT 로 계획 생성 후 영속화. 실패 시 표준 fallback 으로라도 계획을 만든다."""
    ckey = crop_key(payload.itemCode, payload.kindCode)

    try:
        await ensure_crop_ingested(
            payload.itemCode,
            payload.kindCode,
            payload.cropName,
            group_name=None,
        )
        context = await _gather_context(ckey)
    except Exception as e:  # noqa: BLE001 - 인제스트 실패해도 fallback 계획은 제공
        log.info("인제스트/컨텍스트 실패 → fallback crop=%s reason=%s", payload.cropName, e)
        context = ""

    # 공통 지식 허브(knowledge)의 월별 작업 컨텍스트를 보강 주입.
    # 추천·캘린더가 같은 허브를 공유하도록 하는 진입점이며, 실패 시 빈 문자열.
    calendar_ctx = await knowledge.get_calendar_tasks(
        payload.itemCode, payload.kindCode, payload.cropName
    )
    if calendar_ctx:
        block = f"## 월별 표준 작업 (지식 허브)\n{calendar_ctx}"
        context = f"{context}\n\n{block}" if context else block

    raw = await _gpt_tasks(payload, context)
    tasks = _normalize_tasks(raw) or _fallback_tasks()

    # 방문 요일이 지정되면 단기 작업을 방문일로 스냅한 뒤 재정렬·order 재부여
    _snap_to_visit_days(tasks, payload.startDate, payload.visitDays)
    tasks.sort(key=lambda t: t.day_offset)
    for i, t in enumerate(tasks):
        t.order = i

    plan = FarmPlan(
        start_date=payload.startDate,
        crop_item_code=payload.itemCode,
        crop_kind_code=payload.kindCode,
        crop_name=payload.cropName,
        region=payload.region,
        province=payload.province,
        area=payload.area,
        area_unit=payload.areaUnit,
        visit_frequency=payload.visitFrequency,
        visit_days=payload.visitDays,
        track_progress=False,
        tasks=tasks,
    )
    session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return plan
