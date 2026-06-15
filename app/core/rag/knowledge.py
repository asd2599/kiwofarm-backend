"""작목 재배지식 공통 허브.

작물 추천(llm_reason)·영농 캘린더(farmplan)·디지털트윈(twin)이 공통으로 쓰는
RAG 컨텍스트 진입점. 각 함수는 용도별 질의로 로컬 임베딩 스토어에서 청크를 회수해
하나의 문자열로 합성한다.

설계 원칙 (서비스 중단 방지):
  - 인제스트·검색 어느 단계에서든 예외가 나도 빈 문자열로 수렴 (raise 안 함).
    → 호출자(추천·계획·트윈)는 컨텍스트가 비어도 자체 fallback 으로 동작한다.

내부 흐름 (세 함수 공통):
  1. ingest.ensure_crop_ingested() — 없으면 농사로 PDF 다운 + 임베딩 (idempotent)
  2. retrieve.retrieve() — 로컬 numpy 코사인 검색 k=6
  3. "\n\n".join(chunks) 반환

임베딩이 로컬 파일 스토어로 옮겨가면서 DB 세션이 필요 없어졌다.
"""

from __future__ import annotations

import logging

from app.core.rag import retrieve as rag_retrieve
from app.core.rag.ingest import crop_key, ensure_crop_ingested

log = logging.getLogger(__name__)

_K = 6  # 회수 청크 수


async def _context(
    item_code: str,
    kind_code: str,
    crop_name: str,
    group_name: str | None,
    query: str,
) -> str:
    """공통 흐름: 인제스트 보장 → 로컬 코사인 검색 → 청크 합성. 실패 시 빈 문자열."""
    try:
        await ensure_crop_ingested(item_code, kind_code, crop_name, group_name=group_name)
        ckey = crop_key(item_code, kind_code)
        chunks = await rag_retrieve.retrieve(ckey, query, k=_K)
    except Exception as e:  # noqa: BLE001 - 어떤 실패든 컨텍스트 없이 서비스는 계속
        log.info("knowledge context 실패 crop=%s reason=%s", crop_name, e)
        return ""
    return "\n\n".join(c for c in chunks if c)


async def get_cultivation_context(
    item_code: str,
    kind_code: str,
    crop_name: str,
    group_name: str | None = None,
) -> str:
    """재배 환경·수익성·난이도 컨텍스트 (작물 추천용)."""
    return await _context(
        item_code,
        kind_code,
        crop_name,
        group_name,
        "재배 환경 기후 토양 수익성 난이도 시설 조건",
    )


async def get_calendar_tasks(
    item_code: str,
    kind_code: str,
    crop_name: str,
    group_name: str | None = None,
) -> str:
    """월별 작업 일정 컨텍스트 (영농캘린더용)."""
    return await _context(
        item_code,
        kind_code,
        crop_name,
        group_name,
        "월별 작업 파종 정식 수확 시기 생육 단계 시비 관수",
    )


__all__ = [
    "get_cultivation_context",
    "get_calendar_tasks",
]
