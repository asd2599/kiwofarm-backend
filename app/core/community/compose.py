"""작물 일지(메모) → 블로그형 자랑글 초안 자동 생성 (GPT).

캘린더에 날짜별로 남긴 메모를 심기~수확 여정의 따뜻한 1인칭 글로 재구성한다.
메모에 있는 내용만 쓰고 없는 사실은 만들지 않는다. 사진은 별도로 첨부된다.
"""

from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI

from app.config import settings

log = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

PHOTO_MARKER = "[[사진]]"


def _distribute_photos(content: str, n: int) -> str:
    """본문 문단 경계에 사진 마커 n개를 고르게 분산해 '문단→사진' 인터리브를 보장.

    AI 가 찍어 보낸 마커 위치는 신뢰하지 않는다(자주 본문 끝에 몰아넣어 사진이 줄줄이
    나오는 문제). 기존 마커를 모두 떼고 문단 수에 맞춰 재배치한다. 사진과 문단은 둘 다
    시간순이라, k번째 사진이 그 시점 문단 근처에 놓인다.
    """
    cleaned = content.replace(PHOTO_MARKER, "\n\n")
    paras = [p.strip() for p in re.split(r"\n{2,}", cleaned) if p.strip()]
    if n <= 0:
        return "\n\n".join(paras)
    if not paras:
        return "\n\n".join([PHOTO_MARKER] * n)
    # 각 사진을 어느 문단 뒤에 둘지: 문단 인덱스를 고르게 배분(0..P-1).
    after: dict[int, int] = {}
    p_count = len(paras)
    for k in range(n):
        idx = min(p_count - 1, (k * p_count) // n)
        after[idx] = after.get(idx, 0) + 1
    out: list[str] = []
    for i, para in enumerate(paras):
        out.append(para)
        out.extend([PHOTO_MARKER] * after.get(i, 0))
    return "\n\n".join(out)

_SYSTEM = """\
당신은 텃밭 재배 일지를 따뜻하고 생생한 블로그 자랑글로 정리하는 작가입니다.
사용자가 작물을 기르며 날짜별로 남긴 메모를 바탕으로, 심기부터 수확까지의 여정을
자연스러운 1인칭 존댓말로 풀어내세요.

규칙:
- 메모에 있는 내용만 재구성하세요. 없는 사실(수확량·맛 등)을 지어내지 마세요.
- 짧고 산뜻한 제목 1개 + 본문은 시간 흐름이 느껴지는 짧은 문단 여러 개(빈 줄로 구분).
- 과하지 않게 이모지 약간 허용. 본문은 700자 이내.
- 사진 배치: 첨부 사진이 N장이면 사진이 들어갈 시점마다 문단을 두어 사진과 글이 번갈아
  나오게, N개 안팎의 문단을 쓰세요(사진 1장 ≈ 그 시점을 설명하는 문단 1개). 사진 자리엔
  "[[사진]]"을 빈 줄로 분리해 넣되, 절대 본문 맨 끝에 몰아넣지 마세요. N=0이면 표시 없음.
JSON 으로만 답합니다: {"title": str, "content": str}"""


class ComposeError(Exception):
    """초안 생성 불가(키 없음·메모 없음·API 실패)."""


async def compose_brag(
    crop_name: str, dated_memos: list[tuple[str, str]], photo_count: int = 0
) -> dict:
    """dated_memos=[(YYYY-MM-DD, content), ...] 시간순 → {title, content}.

    content 에는 사진 자리표시 [[사진]] 가 photo_count 개 섞여 들어간다(시간순 채움).
    """
    if not settings.openai_api_key:
        raise ComposeError("AI 사용 불가")
    timeline = "\n".join(f"- {d}: {c}" for d, c in dated_memos if c.strip())
    if not timeline:
        raise ComposeError("정리할 메모가 없어요")
    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=1)
    try:
        resp = await client.chat.completions.create(
            model=_MODEL,
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"작물: {crop_name}\n첨부 사진 수: {photo_count}장(시간순)\n\n"
                        f"재배 일지(시간순):\n{timeline}\n\n"
                        f"위 일지를 블로그 자랑글로 정리하고, '[[사진]]' 표시를 정확히 "
                        f"{photo_count}개 넣어 JSON 으로 답하세요."
                    ),
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:  # noqa: BLE001 - 호출·파싱 실패 수렴
        raise ComposeError(f"초안 생성 실패: {e}") from e
    content = str(data.get("content", "")).strip()
    # 사진은 문단 경계에 고르게 재배치(AI 의 끝-몰아넣기 보정 + 정확히 photo_count 개 보장).
    content = _distribute_photos(content, photo_count)
    return {"title": str(data.get("title", "")).strip(), "content": content}
