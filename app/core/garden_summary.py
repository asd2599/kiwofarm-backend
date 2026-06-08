"""텃밭가꾸기 본문 → GPT 키포인트 요약 (재배 정보 화면용).

글 목록을 클릭해 읽는 대신, 작물명으로 텃밭가꾸기 본문을 모아 GPT로 재배 핵심을
요약한다. 본문이 없으면 작물명 일반지식 요약으로 폴백한다. 작물명 단위 메모리 캐시.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.crops.summary import _gpt_call, _gpt_summarize_general
from app.data import nongsaro_garden as garden

log = logging.getLogger(__name__)

_MAX_CONTEXT = 9000  # GPT 입력 컨텍스트 상한(문자)

# 작물명 → (headline, key_points, mode). 프로세스 수명 캐시.
_cache: dict[str, tuple[str, list[str], str]] = {}
_locks: dict[str, asyncio.Lock] = {}

_SYSTEM_PROMPT = (
    "당신은 한국 텃밭·도시농업 재배 전문가입니다. 아래 농사로 '텃밭가꾸기' 자료를 바탕으로 "
    "초보자가 따라 할 수 있게 재배 핵심을 요약합니다. 자료에 없는 내용은 일반 재배 상식 "
    "범위에서만 보완하고, 과장 없이 실용적으로 작성하세요. 출력은 JSON 객체 하나만: "
    '{"headline": "한 줄 요약 (60자 이내)", "key_points": ["키포인트 1", ...]}. '
    "key_points 는 정확히 6~8개. 파종·정식 시기, 모종/씨앗 선택, 물·관수, 거름·비료, "
    "병해충 관리, 수확 시기를 가능한 한 포함하고, 각 항목은 80자 이내 단문으로 작성."
)


def _build_prompt(crop_name: str, context: str) -> str:
    return (
        f"작물: {crop_name}\n\n"
        f"--- 텃밭가꾸기 자료 ---\n{context}\n--- 끝 ---\n\n"
        "위 자료를 바탕으로 지정한 JSON 형식의 재배 요약을 작성하세요."
    )


def _get_lock(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


async def summarize_garden(crop_name: str) -> tuple[str, list[str], str]:
    """작물명 → (headline, key_points, mode).

    mode: 'garden'(텃밭 본문 기반) | 'general'(본문 없어 작물명 일반지식 폴백).
    SummaryError(OPENAI 키 누락·응답 불량 등)는 호출자에서 처리.
    """
    key = crop_name.strip()
    cached = _cache.get(key)
    if cached is not None:
        return cached

    async with _get_lock(key):
        cached = _cache.get(key)
        if cached is not None:
            return cached

        texts = await garden.fetch_garden_texts(key, limit=6)
        context = "\n\n".join(texts)[:_MAX_CONTEXT].strip()
        if context:
            headline, points = await _gpt_call(_SYSTEM_PROMPT, _build_prompt(key, context))
            mode = "garden"
        else:
            headline, points = await _gpt_summarize_general(key, None)
            mode = "general"

        result = (headline, points, mode)
        _cache[key] = result
        return result
