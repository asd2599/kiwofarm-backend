"""작목 농업기술길잡이 PDF → GPT-4o 키포인트 요약.

흐름:
  1. cultivation 라우트가 매칭한 첫 ebook 의 file_url 로 PDF 다운로드
  2. pypdf 로 텍스트 추출 (앞 60p 까지)
  3. GPT-4o 에 작목 컨텍스트와 함께 보내 키포인트 6~8개 + 핵심 한 줄 생성
  4. (item_code, kind_code) 단위 메모리 캐시

OPENAI_API_KEY 미설정 시 명시적 에러 → 라우트가 503 으로 매핑.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from openai import APIError, AsyncOpenAI

from app.config import settings
from app.data import nongsaro

log = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"  # 비용·속도 우선. 품질 더 필요하면 "gpt-4o" 로 교체.
MAX_TEXT_CHARS = 18_000  # 모델 컨텍스트 절약 (한글 18k ≈ 9~10k tokens)


class SummaryError(RuntimeError):
    """요약 실패 (OpenAI 호출·PDF 다운·텍스트 추출 등)."""


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
    text_chars: int
    mode: str  # "pdf" | "general" — PDF 본문 사용 여부


# (item_code, kind_code) -> CropSummary
_cache: dict[tuple[str, str], CropSummary] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _get_lock(key: tuple[str, str]) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


SYSTEM_PROMPT_PDF = (
    "당신은 농업기술 전문가입니다. 농촌진흥청 농업기술길잡이 PDF 본문에서 "
    "초보 농가가 가장 먼저 알아야 할 키포인트를 한국어로 추출합니다. "
    "출력은 JSON 객체 하나만, 다른 텍스트 없이. 형식: "
    '{"headline": "한 줄 요약 (60자 이내)", '
    '"key_points": ["키포인트 1", "키포인트 2", ...]}. '
    "key_points 는 정확히 6~8개. 각 항목은 80자 이내 단문. "
    "재배환경·핵심작업·시비·물관리·병해충·수확·저장·주의점 중에서 골고루 다룹니다. "
    "본문에 없는 정보는 추측하지 말 것."
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


def _build_user_prompt_pdf(crop_name: str, sub_category_name: str | None, text: str) -> str:
    sub = f" ({sub_category_name})" if sub_category_name else ""
    return (
        f"작목: {crop_name}{sub}\n\n"
        f"--- 농업기술길잡이 본문 (앞부분) ---\n{text}\n--- 끝 ---\n\n"
        "위 본문을 바탕으로 JSON 키포인트를 출력하세요."
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


async def _gpt_summarize_pdf(
    crop_name: str, sub_category_name: str | None, text: str
) -> tuple[str, list[str]]:
    return await _gpt_call(
        SYSTEM_PROMPT_PDF,
        _build_user_prompt_pdf(crop_name, sub_category_name, text),
    )


async def _gpt_summarize_general(
    crop_name: str, sub_category_name: str | None
) -> tuple[str, list[str]]:
    return await _gpt_call(
        SYSTEM_PROMPT_GENERAL,
        _build_user_prompt_general(crop_name, sub_category_name),
    )


async def build_summary(
    item_code: str,
    kind_code: str,
    crop_name: str,
    sub_category_name: str | None,
    ebook_code: str | None,
    ebook_name: str | None,
    file_url: str | None,
) -> CropSummary:
    """PDF 우선 → 실패 시 GPT 일반 지식 fallback. 결과는 (item, kind) 단위 캐시.

    PDF 경로가 막힌 케이스 (농사로 다운로드 endpoint 폐쇄 / 스캔본 / 미등록):
      file_url 없거나 download_pdf/extract_pdf_text 가 실패하면 자동으로
      작목명·sub_category 만으로 GPT 키포인트 생성 (mode="general").
    """
    key = (item_code, kind_code)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    async with _get_lock(key):
        cached = _cache.get(key)
        if cached is not None:
            return cached

        pdf_text: str = ""
        pdf_error: str | None = None

        if file_url:
            try:
                log.info("crop summary PDF attempt crop=%s url=%s", crop_name, file_url)
                pdf_bytes = await nongsaro.download_pdf(file_url)
                pdf_text = nongsaro.extract_pdf_text(pdf_bytes)
                if not pdf_text:
                    pdf_error = "PDF 텍스트 추출 결과가 비어있음 (스캔본 가능)"
            except nongsaro.NongsaroError as e:
                pdf_error = str(e)
                log.info("crop summary PDF fallback crop=%s reason=%s", crop_name, pdf_error)
        else:
            pdf_error = "농사로 길잡이에 file_url 이 없음"

        if pdf_text:
            headline, key_points = await _gpt_summarize_pdf(
                crop_name, sub_category_name, pdf_text[:MAX_TEXT_CHARS]
            )
            mode = "pdf"
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
            text_chars=len(pdf_text),
            mode=mode,
        )
        _cache[key] = summary
        log.info(
            "crop summary build done crop=%s mode=%s points=%d", crop_name, mode, len(key_points)
        )
        return summary
