"""GPT-4o 멀티모달 재배 일지 판정 — 캘린더 메모·사진 누적 기록으로 수확 인증.

단일 사진 판정(verify.judge_photo)과 달리, 계획에 쌓인 날짜별 메모 텍스트와
시간순 사진들을 한 번에 보여주고 '실제로 길러서 수확했는가'를 판정한다.
관대 정책 동일: crop_match && has_harvest && !fake_suspect 면 통과.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date

from app.core.harvest.verify import MODEL, VerifyError, _get_client

log = logging.getLogger(__name__)

# 한 판정에 보낼 최대 사진 수 — 생육 과정(앞쪽) + 수확 정황(뒤쪽) 위주로 추린다.
MAX_PHOTOS = 8

SYSTEM = """\
당신은 도시 텃밭 수확 인증 심사관입니다. 사용자가 작물을 기르며 캘린더에
남긴 재배 일지(날짜별 메모)와 시간순 사진들을 보고, 지정 작물을 실제로
길러서 수확까지 했는지 판정합니다. JSON 으로만 답합니다.

판정 기준:
- crop_match: 사진 속 작물이 지정 작물과 일치하는가
- growth_consistent: 사진들이 같은 작물의 생육 과정(시간 흐름)으로 자연스러운가
- has_harvest: 수확 정황이 있는가 (수확물을 손에 들거나 담은 사진,
  메모의 수확 언급 포함. 아직 기르는 중이기만 하면 false)
- fake_suspect: 모니터/인쇄물 재촬영, 스톡사진, 서로 무관한 사진 짜깁기 등
  직접 재배가 아닌 정황이 보이면 true
- quantity: 눈으로 추정한 수확량 (예: "상추 약 10장", "방울토마토 한 줌")
- confidence: 전체 판정 확신도 0.0~1.0
- reason: 사용자에게 보여줄 한 문장 (친근한 존댓말)
- summary: 재배 여정 한두 문장 요약 (도감 기록용, 존댓말)"""


@dataclass(frozen=True)
class JournalEntry:
    """판정 입력 1건 — 메모 텍스트(빈 문자열 가능) + 그날 사진들."""

    memo_date: date
    content: str
    photos: list[tuple[bytes, str]]  # (bytes, mime)


@dataclass(frozen=True)
class JournalVerdict:
    crop_match: bool
    growth_consistent: bool
    has_harvest: bool
    fake_suspect: bool
    quantity: str
    confidence: float
    reason: str
    summary: str

    @property
    def passed(self) -> bool:
        return self.crop_match and self.has_harvest and not self.fake_suspect

    def as_dict(self) -> dict:
        return asdict(self)


def _pick_photos(
    photos: list[tuple[date, bytes, str]],
) -> list[tuple[date, bytes, str]]:
    """시간순 사진에서 최대 MAX_PHOTOS 장 — 초반 2 + 중반 균등 2 + 막판 4.

    수확 증거는 대체로 마지막 사진에 있고, 초반 사진은 생육 연속성 확인용.
    """
    if len(photos) <= MAX_PHOTOS:
        return photos
    head, tail = photos[:2], photos[-4:]
    middle = photos[2:-4]
    step = max(1, len(middle) // 2)
    mid = middle[::step][:2]
    return head + mid + tail


async def judge_journal(
    crop_name: str,
    start_date: date,
    entries: list[JournalEntry],
) -> JournalVerdict:
    """일지 전체 → 판정. 사진이 1장도 없거나 호출 실패 시 VerifyError."""
    photos: list[tuple[date, bytes, str]] = [
        (e.memo_date, b, m) for e in entries for (b, m) in e.photos
    ]
    photos.sort(key=lambda x: x[0])
    if not photos:
        raise VerifyError("판정할 사진이 없습니다")
    picked = _pick_photos(photos)

    timeline = "\n".join(
        f"- {e.memo_date.isoformat()}: "
        + (e.content.strip() or "(메모 없음)")
        + (f" [사진 {len(e.photos)}장]" if e.photos else "")
        for e in sorted(entries, key=lambda e: e.memo_date)
    )
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"지정 작물: {crop_name}\n"
                f"재배 시작일: {start_date.isoformat()}\n"
                f"재배 일지:\n{timeline}\n\n"
                f"아래는 일지 사진 {len(picked)}장(시간순, 전체 {len(photos)}장 중)"
                "입니다. 일지와 사진을 종합해 판정해 JSON 으로 답하세요. 형식: "
                '{"crop_match": bool, "growth_consistent": bool, "has_harvest": bool, '
                '"fake_suspect": bool, "quantity": str, "confidence": 0.0-1.0, '
                '"reason": str, "summary": str}'
            ),
        }
    ]
    for d, b, mime in picked:
        content.append({"type": "text", "text": f"({d.isoformat()} 촬영 기록)"})
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime or 'image/jpeg'};base64,"
                    + base64.b64encode(b).decode()
                },
            }
        )

    try:
        resp = await _get_client().chat.completions.create(
            model=MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": content},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except VerifyError:
        raise
    except Exception as e:  # noqa: BLE001 - 호출·파싱 실패를 한 타입으로 수렴
        raise VerifyError(f"멀티모달 일지 판정 실패: {e}") from e

    return JournalVerdict(
        crop_match=bool(data.get("crop_match", False)),
        growth_consistent=bool(data.get("growth_consistent", False)),
        has_harvest=bool(data.get("has_harvest", False)),
        fake_suspect=bool(data.get("fake_suspect", False)),
        quantity=str(data.get("quantity", "")),
        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0))),
        reason=str(data.get("reason", "")),
        summary=str(data.get("summary", "")),
    )
