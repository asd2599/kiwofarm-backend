"""RAG 기반 영농 캘린더(농사계획) 생성.

흐름:
  1. ensure_crop_ingested 로 작목 청크 확보 (농사로 PDF → 임베딩, 실패 시 GPT general).
  2. facet 별 RAG 질의로 관련 청크 회수 → 컨텍스트 합성.
  3. GPT(json) 로 시작일 기준 상대 오프셋(day_offset) task 리스트 생성.
  4. GPT/RAG 실패 시 표준 재배 단계 기반 결정론적 fallback.
  5. FarmPlan + FarmTask 영속화.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.rag import knowledge
from app.core.rag import retrieve as rag_retrieve
from app.core.farmplan.farmwork import schedule_for
from app.core.rag.ingest import crop_key, ensure_crop_ingested
from app.db.models.farm_plan import FarmPlan, FarmTask
from app.schemas.farmplan import FarmPlanCreate

log = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"  # 비용 고려해 mini 유지. 정확도는 농작업일정 주입·RAG 가중으로 보완.

# 계획 facet → RAG 질의문. 병해충 예방을 명시적 facet 으로 포함.
_FACETS: list[tuple[str, str]] = [
    ("파종/육묘/정식 시기", "파종 육묘 정식 시기와 방법"),
    ("시비", "밑거름 웃거름 시비 시기와 방법"),
    ("물·관수 관리", "물주기 관수 토양 수분 관리"),
    ("병해충 예방", "병해충 예방 시기별 방제 주요 병해충 주의사항"),
    ("수확", "수확 시기와 방법"),
    ("생육 단계별 작업", "월별 생육 단계별 주요 농작업"),
    ("텃밭 재배", "텃밭 도시농업 모종 심는 시기와 방법 소규모 재배 관리"),
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
    """facet 별 청크를 회수해 라벨링된 컨텍스트 블록으로 합성.

    7개 facet 쿼리를 단일 배치 임베딩(retrieve_many)으로 묶어 왕복을 7회→1회로 줄인다.
    """
    try:
        # cultivation·general(GPT 생성) kind 는 낮추고, 진짜 농사로 데이터를 우선시킨다.
        results = await rag_retrieve.retrieve_many_boosted(
            ckey,
            [query for _, query in _FACETS],
            k=4,
            boost={"cultivation": -0.06, "general": -0.06},
        )
    except Exception as e:  # noqa: BLE001 - 컨텍스트 회수 실패해도 fallback 계획은 제공
        log.info("retrieve_many_boosted 실패 reason=%s", e)
        return ""
    blocks: list[str] = []
    for (label, _query), chunks in zip(_FACETS, results):
        if chunks:
            joined = "\n".join(f"- {c}" for c in chunks)
            blocks.append(f"## {label}\n{joined}")
    return "\n\n".join(blocks)


def _conditions_block(payload: FarmPlanCreate) -> str:
    """추천받기에서 넘어온 재배 조건을 프롬프트용 텍스트로. 없으면 빈 문자열."""
    c = payload.growConditions
    if c is None:
        return ""
    lines: list[str] = []
    if c.place:
        lines.append(f"재배 장소: {c.place}")
    if c.facility:
        lines.append(f"보유 시설: {', '.join(c.facility)}")
    if c.sunHours:
        lines.append(f"일조 시간: {c.sunHours}")
    if c.direction:
        lines.append(f"방향: {c.direction}")
    if c.experience:
        lines.append(f"영농 경험: {c.experience}")
    if not lines:
        return ""
    return "재배자 조건:\n" + "\n".join(f"- {x}" for x in lines) + "\n\n"


def _method_place_rules(payload: FarmPlanCreate) -> str:
    """재배 방식(직파/모종)·장소(화분/노지)별 강제 규칙 — 맥락에 안 맞는 작업 차단."""
    rules: list[str] = []
    if payload.growPlace == "pot":
        rules.append(
            "화분·베란다 재배입니다. 노지/밭으로 옮겨 심는 정식, 이랑·두둑 만들기, "
            "멀칭 등 노지 전용 작업은 절대 넣지 마세요. 모든 작업은 화분·플랜터 기준."
        )
    elif payload.growPlace == "field":
        rules.append("텃밭·노지(밭) 재배입니다. 작업은 밭 기준으로 구성하세요.")
    if payload.cultivationMethod == "direct":
        rules.append(
            "직파(씨앗을 재배 장소에 직접 뿌림)입니다. 육묘 트레이·포트 모종 기르기와 "
            "옮겨심기(정식/이식) 작업은 넣지 말고, 파종 후 솎아주기로 관리하세요."
        )
    elif payload.cultivationMethod == "seedling":
        rules.append(
            "모종을 심습니다. 옮겨심기(정식)는 최초 1회만 넣고, 정식 후 다시 다른 곳으로 "
            "옮기는 이중 이식 작업은 절대 넣지 마세요."
        )
    elif payload.cultivationMethod == "germinate":
        rules.append(
            "솜·스펀지·물에 씨앗을 먼저 발아시킨 뒤 싹이 나면 흙으로 옮겨 심습니다. "
            "'씨앗 발아'와 '흙에 옮겨심기(정식)' 단계를 넣되 옮겨심기는 1회만 — "
            "이후 추가 이식은 절대 넣지 마세요."
        )
    if not rules:
        return ""
    return "재배 방식·장소 규칙(반드시 지키세요):\n" + "\n".join(
        f"- {r}" for r in rules
    ) + "\n\n"


def _build_prompt(payload: FarmPlanCreate, context: str) -> str:
    # region 은 보통 "시·도 시·군·구" 전체를 담는다. province 가 이미 포함돼 있으면 중복 접두 방지.
    region = payload.region.strip()
    prov = (payload.province or "").strip()
    if prov and not region.startswith(prov):
        region = f"{prov} {region}".strip()
    if not region:
        region = prov
    conditions = _conditions_block(payload)
    method_rules = _method_place_rules(payload)
    farmwork = schedule_for(crop_key(payload.itemCode, payload.kindCode))
    # 면적 미입력(화분·소규모) 이면 면적 줄 대신 소규모 안내를 넣는다.
    area_line = (
        f"농지 면적: {payload.area} {payload.areaUnit}"
        if payload.area
        else "재배 규모: 화분·소규모(면적 미지정)"
    )
    cond_guide = (
        "재배자 조건(장소·시설·일조·경험 등)을 반드시 반영하세요. "
        "시설(비닐터널·미니온실)이 있으면 노지보다 이르거나 늦은 작기도 가능하고, "
        "베란다·옥상·화분 등 소규모면 면적에 맞춰 작업 강도를 낮추며, "
        "일조가 부족하면(<3h) 그에 맞는 관리·작목 주의를, "
        "영농 경험이 '처음'이면 더 쉬운 표현과 기본 작업 위주로 구성하세요. "
        if conditions
        else ""
    )
    return (
        f"작목: {payload.cropName}\n"
        f"재배 시작일: {payload.startDate.isoformat()}\n"
        f"지역: {region}\n"
        f"{area_line}\n\n"
        f"{conditions}"
        f"{method_rules}"
        f"{farmwork}"
        f"--- 농업기술 참고자료 (농사로 기반) ---\n{context or '(참고자료 없음)'}\n--- 끝 ---\n\n"
        "위 자료를 바탕으로 시작일부터 한 작기(보통 1년 이내)의 농사 일정을 만드세요. "
        "파종(씨뿌리기)·정식(아주심기)·수확 시기는 농작업일정의 월을 반드시 따르세요. "
        "지역이 남부지방이면 농작업일정보다 약간 이르게, 북부·고랭지면 약간 늦게 시기를 조정하세요. "
        "각 작업은 시작일로부터의 day_offset(0=시작일 당일)과 duration_days(작업 지속 일수)로 표현합니다. "
        "관수·생육 관리·육묘처럼 일정 기간 이어지는 작업은 duration_days로 기간을 잡고(예: 14), "
        "파종·옮겨심기·웃거름·방제·수확 같은 단발 작업은 duration_days=1로 하세요. "
        "지역 기후와 면적을 고려해 현실적인 시기를 잡고, 병해충 예방 작업을 반드시 포함하세요. "
        "옮겨심기(정식·이식)는 전체 일정에서 최대 1회만 넣으세요. "
        f"{cond_guide}"
        "일정은 수확까지만 다루고, 수확 후 저장·선별·유통 같은 작업은 넣지 마세요. "
        "출력은 JSON 객체 하나만. 형식: "
        '{"tasks": [{"title": "작업명(30자 이내)", "detail": "구체 방법 80자 이내", '
        '"category": "seeding|growing|fertilize|water|pest|harvest|etc", '
        '"day_offset": 정수, "duration_days": 정수, "source_note": "근거 한 줄"}]}. '
        "작업은 시간순으로 8~16개. day_offset 오름차순 정렬. "
        "농작업일정·참고자료의 시기를 우선해 그대로 따르고, 자료에 없는 작업만 표준 재배력으로 보수적으로 추정하세요."
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
            max_tokens=2000,  # tasks 8~16개 JSON 상한 — 응답 길이·지연 예측 가능
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
                duration_days=duration,  # 기간형(관수·생육 등) 예보 span 허용
                source_note=(str(item.get("source_note") or "").strip() or None),
            )
        )
    tasks.sort(key=lambda t: t.day_offset)
    for i, t in enumerate(tasks):
        t.order = i
    return tasks


def _fallback_tasks(payload: FarmPlanCreate) -> list[FarmTask]:
    """표준 재배 단계 fallback (GPT/RAG 전부 실패 시). 재배 방식·장소에 맞춰 분기.

    - 화분: '밭 준비/노지' 대신 '화분·흙 준비'.
    - 직파: 파종 + 솎아주기(옮겨심기 없음).
    - 발아: 씨앗 발아(솜·스펀지) → 흙에 옮겨심기 1회.
    - 모종/기본: '모종 심기(정식)' 1회.
    단발 작업은 하루(1), 관수·생육·육묘 같은 이어지는 작업은 기간형(예보 span).
    """
    pot = payload.growPlace == "pot"
    method = payload.cultivationMethod
    prep = ("화분·흙 준비", "fertilize") if pot else ("밭 준비 · 토양 정비", "fertilize")

    # (title, category, day_offset, duration) — duration>1 = 기간형 예보 span
    spec: list[tuple[str, str, int, int]] = []
    if method == "germinate":
        spec.append(("씨앗 발아 (솜·스펀지)", "seeding", 0, 1))
        spec.append((prep[0], prep[1], 3, 1))
        spec.append(("육묘 관리 (싹 키우기)", "growing", 1, 6))
        spec.append(("싹 나면 흙에 옮겨심기", "seeding", 7, 1))
    elif method == "direct":
        spec.append((prep[0], prep[1], 0, 1))
        spec.append(("파종 · 씨앗 직접 뿌리기", "seeding", 5, 1))
        spec.append(("솎아주기 · 간격 조절", "growing", 20, 1))
    else:  # seedling/기본
        spec.append((prep[0], prep[1], 0, 1))
        spec.append(("모종 심기 (정식)", "seeding", 5, 1))
    spec += [
        ("관수 · 물 관리", "water", 12, 14),
        ("1차 웃거름", "fertilize", 30, 1),
        ("병해충 예찰 · 예방", "pest", 42, 1),
        ("생육 관리", "growing", 55, 21),
        ("2차 웃거름", "fertilize", 72, 1),
        ("병해충 정기 방제", "pest", 88, 1),
        ("수확", "harvest", 100, 1),
    ]
    return [
        FarmTask(
            title=title,
            detail=None,
            category=cat,
            day_offset=off,
            duration_days=dur,
            order=i,
            source_note="표준 재배력 기반 기본 일정",
        )
        for i, (title, cat, off, dur) in enumerate(spec)
    ]


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


async def generate_plan(
    session: AsyncSession, payload: FarmPlanCreate, device_id: str
) -> FarmPlan:
    """RAG+GPT 로 계획 생성 후 영속화. 실패 시 표준 fallback 으로라도 계획을 만든다."""
    ckey = crop_key(payload.itemCode, payload.kindCode)

    try:
        await ensure_crop_ingested(
            payload.itemCode,
            payload.kindCode,
            payload.cropName,
            group_name=None,
        )
    except Exception as e:  # noqa: BLE001 - 인제스트 실패해도 fallback 계획은 제공
        log.info("인제스트 실패 → fallback crop=%s reason=%s", payload.cropName, e)

    # facet 컨텍스트와 지식 허브의 월별 작업 컨텍스트를 동시에 회수(임베딩 왕복 겹침).
    # 둘 다 자체적으로 예외를 흡수해 빈 문자열로 수렴하므로 gather 가 실패하지 않는다.
    context, calendar_ctx = await asyncio.gather(
        _gather_context(ckey),
        knowledge.get_calendar_tasks(
            payload.itemCode, payload.kindCode, payload.cropName
        ),
    )

    if calendar_ctx:
        block = f"## 월별 표준 작업 (지식 허브)\n{calendar_ctx}"
        context = f"{context}\n\n{block}" if context else block

    raw = await _gpt_tasks(payload, context)
    tasks = _normalize_tasks(raw) or _fallback_tasks(payload)

    # 방문 요일이 지정되면 단기 작업을 방문일로 스냅한 뒤 재정렬·order 재부여
    _snap_to_visit_days(tasks, payload.startDate, payload.visitDays)
    tasks.sort(key=lambda t: t.day_offset)
    for i, t in enumerate(tasks):
        t.order = i

    plan = FarmPlan(
        device_id=device_id,
        start_date=payload.startDate,
        name=(payload.name or "").strip()[:255] or None,
        crop_item_code=payload.itemCode,
        crop_kind_code=payload.kindCode,
        crop_name=payload.cropName,
        region=payload.region,
        province=payload.province,
        area=payload.area or 0.0,  # 0 = 면적 미지정(화분·소규모)
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
