"""GPT-4o 멀티모달 수확 사진 판정.

판정 항목: 작물 일치 / 수확물 여부(재배 중 사진과 구분) / 신선도 / 수량 추정 /
도용(화면 재촬영·스톡사진) 의심 / 신뢰도. 관대 정책: crop_match && is_harvest
&& !fake_suspect 면 통과. settings.harvest_demo_mode 면 무조건 통과(판정은
그대로 기록해 시연 후 검토 가능).
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict, dataclass

from openai import AsyncOpenAI

from app.config import settings

log = logging.getLogger(__name__)

MODEL = "gpt-4o"

SYSTEM = """\
당신은 도시 텃밭 수확 인증 심사관입니다. 사용자가 올린 사진이 특정 작물의
'직접 수확한 수확물' 사진인지 판정합니다. JSON 으로만 답합니다.

판정 기준:
- crop_match: 사진 속 작물이 지정 작물과 일치하는가
- is_harvest: 수확된 상태인가 (수확물을 손에 들거나 담은 모습 포함.
  아직 밭/화분에서 자라는 중이면 false)
- freshness: 신선도 1(시듦)~5(매우 신선). 판단 불가 시 3
- quantity: 눈으로 추정한 수량 (예: "상추 약 10장", "방울토마토 한 줌")
- fake_suspect: 모니터/인쇄물 재촬영, 스톡사진·일러스트, 마트 매대 등
  직접 수확이 아닌 정황이 보이면 true
- confidence: 전체 판정 확신도 0.0~1.0
- reason: 사용자에게 보여줄 한 문장 (친근한 존댓말)"""


@dataclass(frozen=True)
class PhotoVerdict:
    crop_match: bool
    is_harvest: bool
    freshness: int
    quantity: str
    fake_suspect: bool
    confidence: float
    reason: str

    @property
    def passed(self) -> bool:
        return self.crop_match and self.is_harvest and not self.fake_suspect

    def as_dict(self) -> dict:
        return asdict(self)


class VerifyError(RuntimeError):
    """멀티모달 판정 실패 (키 미설정·호출 실패)."""


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if not settings.openai_api_key:
        raise VerifyError("OPENAI_API_KEY 미설정")
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def judge_photo(
    photo_bytes: bytes, mime: str, crop_name: str
) -> PhotoVerdict:
    """사진 1장 → 판정. 실패 시 VerifyError."""
    b64 = base64.b64encode(photo_bytes).decode()
    try:
        resp = await _get_client().chat.completions.create(
            model=MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"지정 작물: {crop_name}\n이 사진을 판정해 JSON 으로 답하세요. "
                                '형식: {"crop_match": bool, "is_harvest": bool, '
                                '"freshness": 1-5, "quantity": str, "fake_suspect": bool, '
                                '"confidence": 0.0-1.0, "reason": str}'
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}"},
                        },
                    ],
                },
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except VerifyError:
        raise
    except Exception as e:  # noqa: BLE001 - 호출·파싱 실패를 한 타입으로 수렴
        raise VerifyError(f"멀티모달 판정 실패: {e}") from e

    return PhotoVerdict(
        crop_match=bool(data.get("crop_match", False)),
        is_harvest=bool(data.get("is_harvest", False)),
        freshness=max(1, min(5, int(data.get("freshness", 3) or 3))),
        quantity=str(data.get("quantity", "")),
        fake_suspect=bool(data.get("fake_suspect", False)),
        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0))),
        reason=str(data.get("reason", "")),
    )
