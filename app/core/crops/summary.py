"""작목 농업기술길잡이 → RAG 기반 GPT 키포인트 요약.

흐름 (계획 생성기와 동일한 RAG 파이프라인 재사용):
  1. ensure_crop_ingested 로 작목 청크 확보 (농사로 PDF → 청크 → 임베딩,
     PDF 실패 시 GPT general 지식 텍스트). idempotent.
  2. 요약 facet 별 RAG 질의로 의미적으로 관련된 청크만 회수 → 컨텍스트 합성.
  3. GPT 에 회수 컨텍스트를 보내 키포인트 6~8개 + 핵심 한 줄 생성.
  4. store.cultivation_source 로 PDF/general 모드 판정.
  5. (item_code, kind_code) 단위 메모리 캐시.

이전 구현은 PDF 60p 전체를 18k자로 잘라 통째 GPT 로 보냈으나, 이제 계획
생성과 같은 로컬 임베딩 코사인 검색으로 요약에 필요한 청크만 추려 보낸다.

OPENAI_API_KEY 미설정 시 명시적 에러 → 라우트가 503 으로 매핑.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from openai import APIError, AsyncOpenAI

from app.config import settings
from app.core.rag import retrieve as rag_retrieve
from app.core.rag import store
from app.core.rag.ingest import crop_key, ensure_crop_ingested

log = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"  # 비용·속도 우선. 품질 더 필요하면 "gpt-4o" 로 교체.

# 요약 facet → RAG 질의문. 키포인트가 고루 분포하도록 주제별로 회수한다.
_SUMMARY_FACETS: list[str] = [
    "재배 환경 기후 토양 일조 조건",
    "파종 육묘 정식 시기와 방법",
    "밑거름 웃거름 시비 시기와 방법",
    "물주기 관수 토양 수분 관리",
    "병해충 예방 시기별 방제 주요 병해충 주의사항",
    "수확 시기 방법 수확 후 저장",
]
_RETRIEVE_K = 3  # facet 당 회수 청크 수


class SummaryError(RuntimeError):
    """요약 실패 (OpenAI 호출·RAG 인제스트·검색 등)."""


@dataclass(frozen=True)
class CropSummary:
    item_code: str
    kind_code: str
    crop_name: str
    headline: str
    key_points: list[str]
    source_ebook_code: str | None
    source_ebook_name: str | None
    source_file_url: str | None
    text_chars: int  # 회수해 GPT 로 보낸 컨텍스트 길이
    mode: str  # "pdf" | "general" — 적재된 재배지식 청크의 출처


# (item_code, kind_code) -> CropSummary
_cache: dict[tuple[str, str], CropSummary] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _get_lock(key: tuple[str, str]) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


SYSTEM_PROMPT_RAG = (
    "당신은 농업기술 전문가입니다. 농촌진흥청 농업기술길잡이에서 검색된 본문 "
    "발췌(RAG) 를 근거로, 초보 농가가 가장 먼저 알아야 할 키포인트를 한국어로 "
    "추출합니다. 출력은 JSON 객체 하나만, 다른 텍스트 없이. 형식: "
    '{"headline": "한 줄 요약 (60자 이내)", '
    '"key_points": ["키포인트 1", "키포인트 2", ...]}. '
    "key_points 는 정확히 6~8개. 각 항목은 80자 이내 단문. "
    "재배환경·핵심작업·시비·물관리·병해충·수확·저장·주의점 중에서 골고루 다룹니다. "
    "발췌에 없는 정보는 추측하지 말 것."
)

SYSTEM_PROMPT_GENERAL = (
    "당신은 한국 농업기술 전문가입니다. 사용자가 입력한 작목명에 대해 "
    "초보 농가가 가장 먼저 알아야 할 재배 키포인트를 한국어로 정리합니다. "
    "출력은 JSON 객체 하나만, 다른 텍스트 없이. 형식: "
    '{"headline": "한 줄 요약 (60자 이내)", '
    '"key_points": ["키포인트 1", "키포인트 2", ...]}. '
    "key_points 는 정확히 6~8개. 각 항목은 80자 이내 단문. "
    "재배환경(기후·토양·일조)·파종/정식·시비·물관리·병해충·수확·저장·주의점 중에서 골고루 다룹니다. "
    "한국 노지·시설 재배 표준에 맞춰 작성하고, 너무 일반적이거나 추측 정보는 피합니다."
)


def _build_user_prompt_rag(crop_name: str, sub_category_name: str | None, context: str) -> str:
    sub = f" ({sub_category_name})" if sub_category_name else ""
    return (
        f"작목: {crop_name}{sub}\n\n"
        f"--- 농업기술길잡이 검색 발췌 (RAG) ---\n{context}\n--- 끝 ---\n\n"
        "위 발췌를 바탕으로 JSON 키포인트를 출력하세요."
    )


def _build_user_prompt_general(crop_name: str, sub_category_name: str | None) -> str:
    sub = f" (농사로 분류: {sub_category_name})" if sub_category_name else ""
    return (
        f"작목: {crop_name}{sub}\n\n"
        "이 작목의 재배 키포인트를 JSON 으로 출력하세요. "
        "농촌진흥청 표준 재배 권장사항에 가깝게 작성하세요."
    )


def _parse_json_response(content: str) -> tuple[str, list[str]]:
    """모델 응답 JSON → (headline, key_points)."""
    try:
        obj = json.loads(content)
    except json.JSONDecodeError as e:
        raise SummaryError(f"GPT 응답 JSON 파싱 실패: {e}") from e
    headline = (obj.get("headline") or "").strip()
    raw_points = obj.get("key_points") or []
    if not isinstance(raw_points, list):
        raise SummaryError("GPT 응답 key_points 가 배열이 아닙니다.")
    key_points = [str(p).strip() for p in raw_points if str(p).strip()]
    if not headline or len(key_points) < 3:
        raise SummaryError("GPT 응답이 비어있거나 키포인트가 너무 적습니다.")
    return headline, key_points


async def _gpt_call(system_prompt: str, user_prompt: str) -> tuple[str, list[str]]:
    if not settings.openai_api_key:
        raise SummaryError("OPENAI_API_KEY 가 설정되지 않았습니다 (.env 확인)")
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            response_format={"type": "json_object"},
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except APIError as e:
        raise SummaryError(f"OpenAI 호출 실패: {e}") from e

    content = resp.choices[0].message.content or ""
    return _parse_json_response(content)


async def _gpt_summarize_rag(
    crop_name: str, sub_category_name: str | None, context: str
) -> tuple[str, list[str]]:
    return await _gpt_call(
        SYSTEM_PROMPT_RAG,
        _build_user_prompt_rag(crop_name, sub_category_name, context),
    )


async def _gpt_summarize_general(
    crop_name: str, sub_category_name: str | None
) -> tuple[str, list[str]]:
    return await _gpt_call(
        SYSTEM_PROMPT_GENERAL,
        _build_user_prompt_general(crop_name, sub_category_name),
    )


async def _gather_context(ckey: str) -> str:
    """요약 facet 별 청크를 회수해 중복 제거 후 합성한다."""
    seen: set[str] = set()
    blocks: list[str] = []
    for query in _SUMMARY_FACETS:
        try:
            chunks = await rag_retrieve.retrieve(ckey, query, k=_RETRIEVE_K)
        except Exception as e:  # noqa: BLE001 - 한 facet 검색 실패가 전체를 막지 않게
            log.info("summary retrieve 실패 ckey=%s reason=%s", ckey, e)
            continue
        for c in chunks:
            if c and c not in seen:
                seen.add(c)
                blocks.append(c)
    return "\n".join(f"- {b}" for b in blocks)


async def build_summary(
    item_code: str,
    kind_code: str,
    crop_name: str,
    sub_category_name: str | None,
    ebook_code: str | None,
    ebook_name: str | None,
    file_url: str | None,
    group_name: str | None = None,
) -> CropSummary:
    """RAG 로 작목 청크를 확보·회수해 GPT 키포인트 요약. (item, kind) 단위 캐시.

    인제스트 파이프라인이 PDF 다운→청크→임베딩(또는 PDF 실패 시 GPT general
    지식)을 처리하므로, 여기서는 요약에 필요한 청크만 검색해 GPT 로 보낸다.
    RAG 컨텍스트가 비면(인제스트 실패 등) 작목명 기반 GPT general 로 폴백한다.
    """
    key = (item_code, kind_code)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    async with _get_lock(key):
        cached = _cache.get(key)
        if cached is not None:
            return cached

        ckey = crop_key(item_code, kind_code)
        context = ""
        try:
            await ensure_crop_ingested(
                item_code, kind_code, crop_name, group_name=group_name
            )
            context = await _gather_context(ckey)
        except Exception as e:  # noqa: BLE001 - 인제스트/검색 실패해도 general 폴백
            log.info("summary RAG 인제스트/검색 실패 crop=%s reason=%s", crop_name, e)

        if context:
            headline, key_points = await _gpt_summarize_rag(
                crop_name, sub_category_name, context
            )
            source = store.cultivation_source(ckey)
            mode = "general" if source in (None, "general") else "pdf"
        else:
            headline, key_points = await _gpt_summarize_general(
                crop_name, sub_category_name
            )
            mode = "general"

        summary = CropSummary(
            item_code=item_code,
            kind_code=kind_code,
            crop_name=crop_name,
            headline=headline,
            key_points=key_points,
            source_ebook_code=ebook_code,
            source_ebook_name=ebook_name,
            source_file_url=file_url,
            text_chars=len(context),
            mode=mode,
        )
        _cache[key] = summary
        log.info(
            "crop summary build done crop=%s mode=%s points=%d ctx_chars=%d",
            crop_name,
            mode,
            len(key_points),
            len(context),
        )
        return summary
