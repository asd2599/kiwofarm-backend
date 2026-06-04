"""주간 영농 코칭 한 줄 생성.

이번 주 할 일(작업 제목)을 보고 gpt-4o-mini 가 격려+실용 팁을 담은 한 문장을 만든다.
OPENAI_API_KEY 가 없거나 호출 실패·작업 없음이면 규칙 기반 문장으로 폴백한다.
"""

from __future__ import annotations

import logging
import re

from openai import AsyncOpenAI

from app.config import settings

log = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_SYS = (
    "당신은 한국어 영농 코치입니다. 이번 주 할 일을 보고 "
    "실용적인 팁을 담아 딱 한 문장(존댓말, 50자 내외)으로 담백하게 코칭하세요. "
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


async def weekly_coaching(crop_name: str, region: str, task_titles: list[str]) -> str:
    """이번 주 작업 제목들 → 코칭 한 줄."""
    if not task_titles:
        return "이번 주는 예정된 작업이 없어요. 작물 상태와 토양 수분을 가볍게 점검해 보세요."
    if not settings.openai_api_key:
        return f"이번 주는 '{task_titles[0]}'부터 차근차근 챙겨보세요."

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=10.0)
    tasks_str = ", ".join(task_titles)
    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            temperature=0.6,
            max_tokens=120,
            messages=[
                {"role": "system", "content": _SYS},
                {
                    "role": "user",
                    "content": (
                        f"작물: {crop_name} ({region})\n"
                        f"이번 주 할 일: {tasks_str}\n한 문장 코칭:"
                    ),
                },
            ],
        )
    except Exception as e:  # noqa: BLE001 - 코칭 실패해도 다이제스트는 제공
        log.info("주간 코칭 LLM 실패: %s", e)
        return f"이번 주는 '{task_titles[0]}'부터 차근차근 해보세요!"

    raw = (resp.choices[0].message.content or "").strip()
    line = _clean(raw.splitlines()[0]) if raw else ""
    return line or f"이번 주는 '{task_titles[0]}'부터 챙겨보세요."
