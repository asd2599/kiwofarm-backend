"""GPT-4o 멀티모달 재배 일지 판정 — 캘린더 메모·사진 누적 기록으로 수확 인증.

단일 사진 판정(verify.judge_photo)과 달리, 계획에 쌓인 날짜별 메모 텍스트와
시간순 사진들을 한 번에 보여주고 '실제로 길러서 수확했는가'를 판정한다.

엄격 정책: 작물 일치 + 생육 흐름 자연스러움 + 관리 연속성(방치 아님) + 수확 정황 +
위조 의심 없음을 모두 만족해야 통과. 파종 직후 장기 무기록·띄엄띄엄 기록(사실상
고사 가능성), 수확 증거 없는 수확 주장은 통과시키지 않는다.
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
당신은 도시 텃밭 수확 인증을 까다롭게 심사하는 심사관입니다. 사용자가 작물을
기르며 캘린더에 남긴 재배 일지(날짜별 메모)와 시간순 사진을 보고, 지정 작물을
'직접 길러서 실제로 수확까지' 했는지 판정합니다. 거짓 인증을 막는 것이 누락보다
중요합니다 — 근거가 불충분하거나 의심스러우면 통과시키지 마세요. JSON 으로만 답합니다.

판정 기준(각 boolean, 근거 없으면 false):
- crop_match: 사진 속 작물이 지정 작물과 일치하는가
- growth_consistent: 사진들이 같은 개체의 파종→생육→수확 흐름으로 자연스럽게
  이어지는가. 생육 단계가 통째로 비거나, 서로 다른 작물·장소가 섞이면 false
- care_consistent: 일지 기록 간격이 작물을 실제로 돌본 수준인가. 파종 직후
  장기간(예: 2주 이상) 무기록이거나, 관리 흔적 없이 띄엄띄엄한 기록뿐이면
  방치로 보아 false (그 사이 시들거나 죽었을 가능성이 높음). 아래에 제공하는
  '경과일·기대 수확일·기록 공백' 수치를 핵심 근거로 삼으세요
- has_harvest: 실제 수확 정황이 분명한가. 수확물을 손에 들거나 담은 사진, 또는
  메모의 명확한 수확 기록이 있어야 true. 아직 기르는 중이거나 수확 증거가
  없는데 수확을 주장하면 false
- fake_suspect: 모니터/인쇄물 재촬영, 스톡사진, 무관한 사진 짜깁기 등 직접
  재배가 아닌 정황이 보이면 true
- quantity: 눈으로 추정한 수확량 (예: "상추 약 10장", "방울토마토 한 줌")
- confidence: 전체 판정 확신도 0.0~1.0
- reason: 사용자에게 보여줄 한 문장 (친근한 존댓말). 통과 못 하면 무엇이
  부족한지(예: 관리 공백·수확 증거 부족) 구체적으로 알려주세요
- summary: 재배 여정 한두 문장 요약 (도감 기록용, 존댓말)

주의: 기대 수확일보다 한참 이른 수확 주장, 큰 무기록 공백은 강한 거짓 신호입니다."""


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
    care_consistent: bool
    has_harvest: bool
    fake_suspect: bool
    quantity: str
    confidence: float
    reason: str
    summary: str

    @property
    def passed(self) -> bool:
        # 작물 일치 + 생육 흐름 자연 + 관리 연속(방치 아님) + 수확 정황 + 위조 의심 없음.
        return (
            self.crop_match
            and self.growth_consistent
            and self.care_consistent
            and self.has_harvest
            and not self.fake_suspect
        )

    def as_dict(self) -> dict:
        return asdict(self)


def _cadence_facts(start_date: date, entries: list[JournalEntry]) -> str:
    """기록 간격을 객관 수치로 요약 — LLM 의 care_consistent 판단 근거.

    실제 기록(메모 텍스트 또는 사진이 있는 날)만 센다. 파종일(start_date)에서
    첫 기록까지의 공백과 기록 사이 최장 공백이 방치 판단의 핵심.
    """
    dated = sorted({e.memo_date for e in entries if e.content.strip() or e.photos})
    if not dated:
        return "기록 없음(메모·사진 0일)"
    points = [start_date, *dated]
    gaps = [(points[i + 1] - points[i]).days for i in range(len(points) - 1)]
    max_gap = max(gaps) if gaps else 0
    initial_gap = (dated[0] - start_date).days
    span = (dated[-1] - start_date).days
    return (
        f"기록일수 {len(dated)}일 · 파종~마지막기록 {span}일 · "
        f"파종 후 첫 기록까지 {initial_gap}일 · 기록 사이 최장 무기록 공백 {max_gap}일"
    )


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
    days_to_harvest: list[int] | None = None,
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
    cadence = _cadence_facts(start_date, entries)
    if days_to_harvest:
        lo = days_to_harvest[0]
        hi = days_to_harvest[-1]
        expected = f"{lo}~{hi}일" if lo != hi else f"{lo}일"
    else:
        expected = "정보 없음"
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"지정 작물: {crop_name}\n"
                f"재배 시작일: {start_date.isoformat()}\n"
                f"이 작물의 일반적 수확 소요: {expected}\n"
                f"기록 간격 분석: {cadence}\n"
                f"재배 일지:\n{timeline}\n\n"
                f"아래는 일지 사진 {len(picked)}장(시간순, 전체 {len(photos)}장 중)"
                "입니다. 일지·기록 간격·사진을 종합해 판정해 JSON 으로 답하세요. 형식: "
                '{"crop_match": bool, "growth_consistent": bool, "care_consistent": bool, '
                '"has_harvest": bool, "fake_suspect": bool, "quantity": str, '
                '"confidence": 0.0-1.0, "reason": str, "summary": str}'
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
        care_consistent=bool(data.get("care_consistent", False)),
        has_harvest=bool(data.get("has_harvest", False)),
        fake_suspect=bool(data.get("fake_suspect", False)),
        quantity=str(data.get("quantity", "")),
        confidence=max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0))),
        reason=str(data.get("reason", "")),
        summary=str(data.get("summary", "")),
    )
