"""수확 인증 API — 사진 검증·수확카드·인증 기록.

POST /harvest/verify   사진 업로드 → 규칙+멀티모달 검증 → 기록 저장 → 카드 반환
GET  /harvest/card/{crop_slug}   카드 데이터만 (검증 없이)
GET  /harvest          인증 기록 목록 (도감·뱃지 집계용)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import storage
from app.core.harvest import card as card_mod
from app.core.harvest import rules
from app.core.harvest.verify import VerifyError, judge_photo
from app.core.planting import matrix
from app.db.models.harvest import HarvestRecord
from app.db.session import get_session
from app.schemas.harvest import (
    HarvestCard,
    HarvestRecordOut,
    HarvestVerifyResponse,
    VerdictOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/harvest", tags=["harvest"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/verify", response_model=HarvestVerifyResponse)
async def verify_harvest(
    session: SessionDep,
    photo: UploadFile = File(...),
    crop_slug: str = Form(...),
    plan_id: int | None = Form(None),
) -> HarvestVerifyResponse:
    crop = matrix.get_crop(crop_slug)
    if crop is None:
        raise HTTPException(status_code=404, detail=f"작물 없음: {crop_slug}")
    mime = photo.content_type or ""
    data = await photo.read()
    await photo.seek(0)
    # 형식·크기 검증 + 디스크 저장은 공용 스토리지 모듈에 위임 (/uploads 정적 서빙).
    rel_path, _size = await storage.save_image(photo, subdir="harvest")

    # 1차 규칙(경고만) — EXIF + 재배 계획 연속성
    warnings: list[str] = []
    warnings += rules.check_exif(data).warnings
    warnings += (await rules.check_continuity(session, plan_id, crop_slug)).warnings

    # 2차 멀티모달 판정
    try:
        verdict = await judge_photo(data, mime, crop["name"])
    except VerifyError as e:
        log.warning("멀티모달 판정 불가: %s", e)
        raise HTTPException(status_code=503, detail="AI 검증을 지금 사용할 수 없습니다") from e

    demo = settings.harvest_demo_mode
    verified = verdict.passed or demo

    record = HarvestRecord(
        plan_id=plan_id,
        crop_slug=crop_slug,
        crop_name=crop["name"],
        photo_path=rel_path,  # 업로드 루트 기준 상대경로 (storage.file_url 로 URL 변환)
        verified=verified,
        confidence=verdict.confidence,
        verdict={**verdict.as_dict(), "warnings": warnings, "demo_mode": demo},
        reason=verdict.reason,
        harvested_at=datetime.now().date(),
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    card_data: HarvestCard | None = None
    if verified:
        built = await card_mod.build_card(crop_slug)
        card_data = HarvestCard(**built) if built else None
        message = f"{crop['name']} 수확을 인정합니다! 🎉"
        if demo and not verdict.passed:
            message += " (데모 모드 통과)"
    else:
        message = verdict.reason or "수확 사진으로 확인되지 않았어요. 다시 찍어 올려주세요."

    return HarvestVerifyResponse(
        verified=verified,
        demoMode=demo,
        recordId=record.id,
        verdict=VerdictOut(**verdict.as_dict()),
        warnings=warnings,
        card=card_data,
        message=message,
    )


@router.get("/card/{crop_slug}", response_model=HarvestCard)
async def get_card(crop_slug: str) -> HarvestCard:
    built = await card_mod.build_card(crop_slug)
    if built is None:
        raise HTTPException(status_code=404, detail=f"작물 없음: {crop_slug}")
    return HarvestCard(**built)


@router.get("", response_model=list[HarvestRecordOut])
async def list_records(
    session: SessionDep, plan_id: int | None = None, verified_only: bool = True
) -> list[HarvestRecordOut]:
    q = select(HarvestRecord).order_by(HarvestRecord.created_at.desc())
    if plan_id is not None:
        q = q.where(HarvestRecord.plan_id == plan_id)
    if verified_only:
        q = q.where(HarvestRecord.verified.is_(True))
    rows = (await session.execute(q)).scalars().all()
    return [
        HarvestRecordOut(
            id=r.id,
            planId=r.plan_id,
            cropSlug=r.crop_slug,
            cropName=r.crop_name,
            verified=r.verified,
            confidence=r.confidence,
            harvestedAt=r.harvested_at,
            createdAt=r.created_at,
        )
        for r in rows
    ]
