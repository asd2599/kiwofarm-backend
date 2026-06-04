"""수확 사진 1차 규칙 검사 — 비용 0, 관대 모드(경고만, 차단 안 함).

EXIF 촬영시각과 재배 계획(farm_plan) 연속성을 검사해 경고 목록을 만든다.
어뮤징(스크린 재촬영·인터넷 사진) 완전 차단은 불가능하므로, 경고는 멀티모달
판정과 함께 신뢰도 참고 자료로만 쓴다.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from PIL import ExifTags, Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.planting import matrix
from app.data import crop_ids
from app.db.models.farm_plan import FarmPlan

log = logging.getLogger(__name__)

_DT_TAGS = {"DateTimeOriginal", "DateTime", "DateTimeDigitized"}
MAX_PHOTO_AGE_DAYS = 14  # 수확 사진은 최근 촬영이어야 자연스럽다
GROWTH_TOLERANCE = 0.5  # 최소 생육일수의 50%만 지나도 통과(관대)


@dataclass
class RuleResult:
    warnings: list[str] = field(default_factory=list)
    taken_at: datetime | None = None
    plan_matched: bool = False


def check_exif(photo_bytes: bytes) -> RuleResult:
    """EXIF 촬영시각 검사. EXIF 없음/오래된 사진/미래 시각은 경고."""
    r = RuleResult()
    try:
        img = Image.open(io.BytesIO(photo_bytes))
        exif = img.getexif()
        if not exif:
            r.warnings.append("사진에 촬영 정보(EXIF)가 없습니다")
            return r
        named = {ExifTags.TAGS.get(k, str(k)): v for k, v in exif.items()}
        raw = next((named[t] for t in _DT_TAGS if named.get(t)), None)
        if not raw:
            r.warnings.append("사진에 촬영 시각 정보가 없습니다")
            return r
        r.taken_at = datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
        now = datetime.now()
        if r.taken_at > now + timedelta(hours=1):
            r.warnings.append("촬영 시각이 미래로 기록되어 있습니다")
        elif now - r.taken_at > timedelta(days=MAX_PHOTO_AGE_DAYS):
            r.warnings.append(
                f"촬영한 지 {(now - r.taken_at).days}일 지난 사진입니다"
            )
    except Exception as e:  # noqa: BLE001 - EXIF 파싱 실패는 경고로만
        log.info("EXIF 파싱 실패: %s", e)
        r.warnings.append("사진 메타데이터를 읽을 수 없습니다")
    return r


def _plan_slug(plan: FarmPlan) -> str | None:
    """farm_plan 의 작물 코드(레거시 KAMIS 또는 슬러그) → 슬러그."""
    code = plan.crop_item_code
    if crop_ids.is_slug(code):
        return code
    return crop_ids.slug_for(code)


async def check_continuity(
    session: AsyncSession, plan_id: int | None, crop_slug: str
) -> RuleResult:
    """재배 계획 연속성: 같은 작물 계획이 있고 생육 기간이 충분히 지났는지."""
    r = RuleResult()
    if plan_id is None:
        r.warnings.append("연결된 재배 계획이 없습니다 (계획 없이 인증)")
        return r
    plan = (
        await session.execute(select(FarmPlan).where(FarmPlan.id == plan_id))
    ).scalar_one_or_none()
    if plan is None:
        r.warnings.append(f"재배 계획 #{plan_id}을 찾을 수 없습니다")
        return r
    if _plan_slug(plan) != crop_slug:
        r.warnings.append(
            f"재배 계획의 작물({plan.crop_name})과 인증 작물이 다릅니다"
        )
        return r
    r.plan_matched = True
    crop = matrix.get_crop(crop_slug)
    days = (datetime.now().date() - plan.start_date).days
    dth = (crop or {}).get("days_to_harvest") or []
    if dth:
        min_days = int(dth[0] * GROWTH_TOLERANCE)
        if days < min_days:
            r.warnings.append(
                f"심은 지 {days}일 — 최소 생육 기간({dth[0]}일)에 못 미칩니다"
            )
    return r
