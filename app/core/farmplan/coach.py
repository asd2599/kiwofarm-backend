"""주간 영농 코칭 멘트 생성 (작업별 한 문장).

이번 주 할 일(작업)마다 그 작물에 맞는 실용 조언 한 문장을 gpt-4o-mini 가 배치로 만든다.
예: "딸기 파종 시 흙의 배수와 비옥도를 확인하고 육묘 관리에 신경 쓰세요."
OPENAI_API_KEY 가 없거나 호출 실패면 규칙 기반 문장으로 폴백한다.
"""

from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI

from app.config import settings

log = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_SYS = (
    "당신은 한국어 영농 코치입니다. 각 농작업에 대해 그 작물에 맞는 실용 조언을 "
    "담백한 한 문장(존댓말, 40~60자)으로 작성합니다. "
    "이모지·이모티콘·특수문자 장식은 절대 쓰지 말고, 과장 없이 자연스럽게. "
    "새로운 작업이나 시기를 지어내지 마세요."
)

# LLM 이 그래도 넣을 수 있는 이모지/그림문자 제거용(텍스트만 남긴다).
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"  # 이모지·그림문자 전반
    "\U00002600-\U000027bf"  # 기타 기호·딩뱃
    "\U00002b00-\U00002bff"  # 기타 기호·화살표
    "\U0000fe00-\U0000fe0f"  # variation selector
    "\U0000200d"  # ZWJ
    "\U00002b50\U00002728"  # ★ ✨ 등
    "]+",
    flags=re.UNICODE,
)


def _clean(text: str) -> str:
    """이모지 제거 후 공백 정리."""
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", text)).strip(" -·")


def _fallback(crop_name: str, title: str) -> str:
    return f"{crop_name} {title} 시 기본 재배 관리에 신경 쓰세요."


async def weekly_task_messages(
    crop_name: str, region: str, task_titles: list[str]
) -> list[str]:
    """이번 주 작업 제목들 → 작업별 코칭 멘트(입력 순서대로 1:1)."""
    if not task_titles:
        return []
    fallback = [_fallback(crop_name, t) for t in task_titles]
    if not settings.openai_api_key:
        return fallback

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=12.0)
    listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(task_titles))
    user = (
        f"작물: {crop_name} ({region})\n"
        f"이번 주 작업:\n{listing}\n\n"
        "각 작업마다 그 작물에 맞는 실용 조언을 한 문장으로 만들어 작업 번호 순서대로 "
        'messages 배열에 담아 JSON 으로만 답하세요. 예: '
        '{"messages": ["딸기 파종 시 흙의 배수와 비옥도를 확인하고 육묘 관리에 신경 쓰세요."]}'
    )
    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            temperature=0.5,
            max_tokens=700,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYS},
                {"role": "user", "content": user},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        msgs = data.get("messages")
    except Exception as e:  # noqa: BLE001 - 멘트 생성 실패해도 다이제스트는 제공
        log.info("주간 작업 멘트 LLM 실패: %s", e)
        return fallback

    if not isinstance(msgs, list) or len(msgs) != len(task_titles):
        return fallback
    return [_clean(str(m)) or fallback[i] for i, m in enumerate(msgs)]


_PEST_SYS = (
    "당신은 한국어 식물의학 코치입니다. 주어진 '병해충 발생정보 회보' 본문에서 "
    "해당 작물(또는 이 시기 공통)에 지금 주의할 병해충과 대응을 추립니다. "
    "회보 본문에 없는 내용은 지어내지 말고 본문 근거로만, 현재 상황처럼 자연스럽게. "
    "이모지·특수문자 장식 금지."
)


async def pest_situation(
    crop_name: str, region: str, period_label: str, source_text: str
) -> tuple[str, str] | None:
    """회보 본문 → (제목, 본문) 현재 병해충 상황 요약. 키 없거나 본문 없으면 None."""
    if not source_text or not settings.openai_api_key:
        return None
    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=15.0)
    user = (
        f"작물: {crop_name} ({region})\n기간: {period_label}\n\n"
        f"[병해충 발생정보 회보 본문]\n{source_text[:5000]}\n\n"
        '아래 JSON 으로만 답하세요. title 은 작물·핵심 병해충 중심 20자 내외, '
        'detail 은 지금 상황과 대응을 1~2문장(60자 내외)으로. '
        '{"title": "...", "detail": "..."}'
    )
    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            temperature=0.4,
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _PEST_SYS},
                {"role": "user", "content": user},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:  # noqa: BLE001 - 실패 시 호출부가 폴백 처리
        log.info("병해충 상황 요약 LLM 실패: %s", e)
        return None
    title = _clean(str(data.get("title", "")))
    detail = _clean(str(data.get("detail", "")))
    if not title or not detail:
        return None
    return title, detail
