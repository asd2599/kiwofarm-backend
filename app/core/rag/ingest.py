"""작목별 RAG 청크 인제스트 (로컬 임베딩 스토어).

흐름 (idempotent):
  1. 스토어에 crop_key 의 cultivation 파일이 있으면 skip.
  2. 없으면 농사로 cropEbook 에서 PDF 텍스트 확보 (app/data/nongsaro.py 재사용).
  3. PDF 실패 시 GPT 로 작목 표준 재배지식(병해충 예방 포함) 텍스트 생성 (general fallback).
  4. 텍스트를 청크로 분할 → 임베딩 → store.save (backend/data/embeddings/*.npy+json).

이전엔 postgres(doc_chunk/pgvector)에 적재했으나, 로컬 파일 스토어로 전환했다.
DB 세션이 필요 없다.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

from app.config import settings
from app.core.rag import store
from app.core.rag.embeddings import embed_texts
from app.data import ncpms, nongsaro

log = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
MAX_CHUNKS = 80  # 임베딩 비용·시간 상한
_GEN_MODEL = "gpt-4o-mini"

# 스토어 kind / source 라벨.
CULTIVATION_KIND = "cultivation"
NCPMS_SOURCE = "ncpms"  # kind 이자 source 라벨


def crop_key(item_code: str, kind_code: str) -> str:
    return f"{item_code}:{kind_code}"


def _chunk_text(text: str) -> list[str]:
    """글자 기준 슬라이딩 윈도우 분할. 한글 본문에 단순·견고."""
    text = " ".join(text.split())  # 공백 정규화
    if not text:
        return []
    chunks: list[str] = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for start in range(0, len(text), step):
        chunk = text[start : start + CHUNK_SIZE].strip()
        if chunk:
            chunks.append(chunk)
        if len(chunks) >= MAX_CHUNKS:
            break
    return chunks


async def _generate_general_text(crop_name: str, sub_category_name: str | None) -> str:
    """PDF 가 없을 때 GPT 로 작목 표준 재배·병해충 예방 지식 텍스트 생성."""
    if not settings.openai_api_key:
        raise nongsaro.NongsaroError("PDF 미확보 + OPENAI_API_KEY 미설정으로 인제스트 불가")
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    sub = f" (농사로 분류: {sub_category_name})" if sub_category_name else ""
    resp = await client.chat.completions.create(
        model=_GEN_MODEL,
        temperature=0.3,
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 한국 농업기술 전문가입니다. 농촌진흥청 표준 재배지침에 맞춰 "
                    "작목의 월별 재배 일정과 병해충 예방을 상세히 한국어로 서술합니다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"작목: {crop_name}{sub}\n\n"
                    "다음 항목을 각각 문단으로 상세히 작성하세요: "
                    "재배 환경(기후·토양·일조), 파종/육묘/정식 시기, "
                    "시비(밑거름·웃거름) 시기와 방법, 물·관수 관리, "
                    "주요 생육 단계별 작업, 병해충 예방(시기별 예방조치·주의 병해충), "
                    "수확 시기와 방법. "
                    "한국 노지·시설 재배 기준으로 구체적인 시기를 포함하세요."
                ),
            },
        ],
    )
    return (resp.choices[0].message.content or "").strip()


async def _fetch_pdf_text(
    crop_name: str, group_name: str | None
) -> tuple[str, str, str | None]:
    """농사로에서 (텍스트, source, sub_category_name) 회수. 실패 시 빈 텍스트."""
    match = await nongsaro.find_sub_category(crop_name, kamis_group_name=group_name)
    if not match:
        return "", "general", None
    sub_name = match.sub_name
    ebooks = await nongsaro.fetch_ebook_list(match.sub_code)
    first = next((e for e in ebooks if e.file_url), None)
    if first is None or not first.file_url:
        return "", "general", sub_name
    try:
        pdf_bytes = await nongsaro.download_pdf(first.file_url)
        text = nongsaro.extract_pdf_text(pdf_bytes)
    except nongsaro.NongsaroError as e:
        log.info("ingest PDF fallback crop=%s reason=%s", crop_name, e)
        return "", "general", sub_name
    return text, first.ebook_code, sub_name


async def _add_chunks(ckey: str, kind: str, source: str, chunks: list[str]) -> int:
    if not chunks:
        return 0
    vectors = await embed_texts(chunks)
    return store.save(ckey, kind, chunks, vectors, source)


async def _ingest_cultivation(
    ckey: str, crop_name: str, group_name: str | None
) -> int:
    text, source, sub_name = "", "general", None
    try:
        text, source, sub_name = await _fetch_pdf_text(crop_name, group_name)
    except nongsaro.NongsaroError as e:
        log.info("ingest nongsaro 카테고리 실패 crop=%s reason=%s", crop_name, e)

    if not text:
        text = await _generate_general_text(crop_name, sub_name)
        source = "general"

    chunks = _chunk_text(text)
    if not chunks:
        raise nongsaro.NongsaroError(f"인제스트할 텍스트가 없습니다 crop={crop_name}")
    return await _add_chunks(ckey, CULTIVATION_KIND, source, chunks)


async def _ingest_ncpms_pests(ckey: str, crop_name: str) -> int:
    """NCPMS 병해충 도감을 청크로 추가. 키 없거나 결과 없으면 0."""
    try:
        pest_texts = await ncpms.fetch_pest_texts(crop_name)
    except Exception as e:  # noqa: BLE001 - 병해충 보강 실패해도 인제스트 전체는 진행
        log.info("NCPMS 병해충 인제스트 실패 crop=%s reason=%s", crop_name, e)
        return 0
    chunks: list[str] = []
    for pt in pest_texts:
        chunks.extend(_chunk_text(pt))
    return await _add_chunks(ckey, NCPMS_SOURCE, NCPMS_SOURCE, chunks)


async def ensure_crop_ingested(
    item_code: str,
    kind_code: str,
    crop_name: str,
    group_name: str | None = None,
) -> int:
    """작목 청크 인제스트(idempotent). 반환: 새로 적재한 청크 수.

    - 재배지식(농사로 PDF→실패 시 GPT general)이 없으면 적재.
    - NCPMS_API_KEY 가 있고 해당 작목의 병해충(ncpms) 청크가 아직 없으면 추가 적재.
      → 나중에 키를 넣으면, 이미 인제스트된 작목도 다음 호출 때 병해충 근거가 보강된다.
    """
    ckey = crop_key(item_code, kind_code)
    added = 0

    if not store.exists(ckey, CULTIVATION_KIND):
        added += await _ingest_cultivation(ckey, crop_name, group_name)

    if settings.ncpms_api_key and not store.exists(ckey, NCPMS_SOURCE):
        added += await _ingest_ncpms_pests(ckey, crop_name)

    return added
