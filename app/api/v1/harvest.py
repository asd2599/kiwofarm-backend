"""수확 인증 API — 일지 검증·수확카드·인증 기록.

POST /harvest/verify-journal   캘린더 일지(메모·사진 누적) 분석 → 인증 → 도감 등록
GET  /harvest/card/{crop_slug}   카드 데이터만 (검증 없이)
GET  /harvest          인증 기록 목록 (도감·뱃지 집계용)
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import DeviceDep
from app.config import settings
from app.core.clock import kst_today
from app.core.harvest import card as card_mod
from app.core.harvest import rules
from app.core.harvest.journal import JournalEntry, judge_journal
from app.core.harvest.verify import VerifyError
from app.core.planting import matrix
from app.core.rewards.badges import achieved_ids, build_badges
from app.core.rewards.points import total_points
from app.db.models.farm_plan import FarmPlan, MemoImage, TaskMemo
from app.db.models.harvest import HarvestRecord
from app.db.session import get_session
from app.api.v1.farmplan import _image_url
from app.schemas.farmplan import MemoImageOut, TaskMemoOut
from app.schemas.harvest import (
    CropJournalOut,
    HarvestCard,
    HarvestJournalResponse,
    HarvestRecordOut,
    JournalVerdictOut,
    JournalVerifyIn,
)
from app.schemas.rewards import BadgeOut

log = logging.getLogger(__name__)

router = APIRouter(prefix="/harvest", tags=["harvest"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _journal_missing(
    verdict, entries: list[JournalEntry], crop_name: str, photo_count: int
) -> list[str]:
    """실패한 판정 기준별로 '무엇을 더 하면 되는지' 구체 안내를 만든다.

    합격 여부는 LLM 종합 판단이라 '정확히 N개 더'를 보장할 수는 없으므로,
    부족한 항목과 현재 수치(사진 장수·기록 일수)를 함께 알려 다음 행동을 돕는다.
    """
    recorded_days = sum(1 for e in entries if e.content.strip() or e.photos)
    tips: list[str] = []
    if not verdict.crop_match:
        tips.append(
            f"📷 사진 속 작물이 인증 작물과 달라 보여요. {crop_name} 을(를) "
            "직접 찍은 사진인지 확인해 주세요."
        )
    if not verdict.growth_consistent:
        tips.append(
            f"🌱 새싹→성장→수확까지 이어지는 사진이 부족해요. 지금 사진 "
            f"{photo_count}장 — 생육 단계가 보이게 3장 이상 남겨주세요."
        )
    if not verdict.care_consistent:
        tips.append(
            f"🗓 꾸준한 기록이 부족해요(현재 기록 {recorded_days}일). 며칠 간격으로 "
            "메모·사진을 더 남겨 관리한 흔적을 보여주세요."
        )
    if not verdict.has_harvest:
        tips.append(
            "🧺 수확 정황이 분명하지 않아요. 수확물을 담거나 손에 든 사진을 "
            "1장 이상 올려주세요."
        )
    if verdict.fake_suspect:
        tips.append(
            "⚠️ 직접 재배가 아닌 듯한 정황이 보여요. 직접 촬영한 사진으로 다시 "
            "시도해 주세요."
        )
    return tips


@router.post("/verify-journal", response_model=HarvestJournalResponse)
async def verify_harvest_journal(
    payload: JournalVerifyIn, session: SessionDep, device: DeviceDep
) -> HarvestJournalResponse:
    """'수확했어요' — 캘린더에 쌓인 메모·사진 일지를 분석해 수확을 인증한다.

    통과하면 harvest_record 에 저장돼 도감·뱃지·점수에 즉시 반영된다.
    사진이 1장도 없으면 인증할 수 없다(422).
    """
    plan = await session.scalar(
        select(FarmPlan)
        .where(FarmPlan.id == payload.planId, FarmPlan.device_id == device)
        .options(
            selectinload(FarmPlan.memos)
            .selectinload(TaskMemo.images)
            .undefer(MemoImage.data)
        )
    )
    if plan is None:
        raise HTTPException(status_code=404, detail="해당 농사계획을 찾을 수 없습니다.")

    slug = rules.plan_slug(plan)
    crop = matrix.get_crop(slug) if slug else None
    if crop is None:
        raise HTTPException(
            status_code=422,
            detail=f"{plan.crop_name} 은(는) 도감 인증 대상 작물(40종)이 아닙니다.",
        )

    # 재인증 가드 — 이미 인증된 텃밭은 중복 적립(점수·뱃지·도감)을 막고 기존
    # 상태를 그대로 돌려준다. UI 버튼도 막지만 더블서밋·직접호출 대비 서버에서 보장.
    existing = await session.scalar(
        select(HarvestRecord)
        .where(
            HarvestRecord.plan_id == plan.id,
            HarvestRecord.device_id == device,
            HarvestRecord.verified.is_(True),
        )
        .limit(1)
    )
    if existing is not None:
        built = await card_mod.build_card(slug)
        return HarvestJournalResponse(
            verified=True,
            demoMode=settings.harvest_demo_mode,
            recordId=existing.id,
            card=HarvestCard(**built) if built else None,
            pointsTotal=await total_points(session, device),
            message=f"{crop['name']} 텃밭은 이미 수확 인증을 마쳤어요.",
        )

    entries = [
        JournalEntry(
            memo_date=m.memo_date,
            content=m.content,
            photos=[
                (img.data, img.content_type or "image/jpeg")
                for img in m.images
                if img.data is not None
            ],
        )
        for m in plan.memos
    ]
    image_ids = [img.id for m in plan.memos for img in m.images if img.data is not None]
    if not image_ids:
        raise HTTPException(
            status_code=422,
            detail="인증할 사진이 없습니다. 기르는 동안 캘린더에 사진을 남겨주세요.",
        )

    # 1차 규칙(경고만) — 생육 기간 연속성
    warnings = (
        await rules.check_continuity(session, plan.id, slug, device)
    ).warnings

    # 2차 멀티모달 일지 판정 — 기대 수확 소요일을 함께 넘겨 관리 연속성·조기수확 판단.
    try:
        verdict = await judge_journal(
            crop["name"], plan.start_date, entries, crop.get("days_to_harvest")
        )
    except VerifyError as e:
        log.warning("멀티모달 일지 판정 불가: %s", e)
        raise HTTPException(status_code=503, detail="AI 검증을 지금 사용할 수 없습니다") from e

    demo = settings.harvest_demo_mode
    verified = verdict.passed or demo

    # 새 뱃지 연출용 — 기록 추가 전 달성 상태 스냅샷
    before_badges = await achieved_ids(session, device) if verified else set()

    record = HarvestRecord(
        device_id=device,
        plan_id=plan.id,
        crop_slug=slug,
        crop_name=crop["name"],
        photo_path=None,  # 일지 인증 — 원본은 memo_image, verdict.image_ids 참조
        verified=verified,
        confidence=verdict.confidence,
        verdict={
            **verdict.as_dict(),
            "source": "journal",
            "image_ids": image_ids,
            "memo_days": len(entries),
            "warnings": warnings,
            "demo_mode": demo,
        },
        reason=verdict.reason,
        harvested_at=kst_today(),
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    card_data: HarvestCard | None = None
    new_badges: list[dict] = []
    if verified:
        built = await card_mod.build_card(slug)
        card_data = HarvestCard(**built) if built else None
        after = await build_badges(session, device)
        new_badges = [b for b in after if b["achieved"] and b["id"] not in before_badges]
        message = f"{crop['name']} 수확을 인정합니다! 도감에 등록했어요 🎉"
        if demo and not verdict.passed:
            message += " (데모 모드 통과)"
    else:
        message = verdict.reason or (
            "일지 기록으로 수확이 확인되지 않았어요. 수확물 사진을 남기고 다시 시도해 주세요."
        )

    missing = (
        []
        if verified
        else _journal_missing(verdict, entries, crop["name"], len(image_ids))
    )

    # 데모로 통과(실제 판정은 실패)면 부정적 요약·수확량이 성공 모달과 모순되므로 비운다.
    verdict_dict = verdict.as_dict()
    if demo and not verdict.passed:
        verdict_dict["summary"] = ""
        verdict_dict["quantity"] = ""

    return HarvestJournalResponse(
        verified=verified,
        demoMode=demo,
        recordId=record.id,
        verdict=JournalVerdictOut(**verdict_dict),
        warnings=warnings,
        card=card_data,
        newBadges=[BadgeOut(**b) for b in new_badges],
        pointsTotal=await total_points(session, device),
        message=message,
        missing=missing,
    )


@router.get("/card/{crop_slug}", response_model=HarvestCard)
async def get_card(crop_slug: str) -> HarvestCard:
    built = await card_mod.build_card(crop_slug)
    if built is None:
        raise HTTPException(status_code=404, detail=f"작물 없음: {crop_slug}")
    return HarvestCard(**built)


@router.get("/crop-journal/{crop_slug}", response_model=CropJournalOut)
async def get_crop_journal(
    crop_slug: str, session: SessionDep, device: DeviceDep
) -> CropJournalOut:
    """도감 '내 기록' 탭 — 이 작물을 키우며 남긴 메모·사진을 최신순으로 모은다.

    한 작물을 여러 번(여러 plan) 키웠다면 전부 합친다. 사진 bytea 는 서빙 API
    로 위임하므로 여기선 로드하지 않는다(메타·URL 만).
    """
    crop = matrix.get_crop(crop_slug)
    plans = (
        await session.scalars(
            select(FarmPlan)
            .where(FarmPlan.device_id == device)
            .options(selectinload(FarmPlan.memos).selectinload(TaskMemo.images))
        )
    ).all()

    memos: list[TaskMemoOut] = []
    photo_count = 0
    for plan in plans:
        if rules.plan_slug(plan) != crop_slug:
            continue
        for memo in plan.memos:
            images = [
                MemoImageOut(
                    id=img.id,
                    url=_image_url(img),
                    originalName=img.original_name,
                    contentType=img.content_type,
                    size=img.size_bytes,
                )
                for img in memo.images
            ]
            photo_count += len(images)
            memos.append(
                TaskMemoOut(
                    id=memo.id,
                    memoDate=memo.memo_date,
                    content=memo.content,
                    images=images,
                )
            )

    memos.sort(key=lambda m: m.memoDate, reverse=True)
    return CropJournalOut(
        cropSlug=crop_slug,
        cropName=crop["name"] if crop else crop_slug,
        totalMemos=len(memos),
        totalPhotos=photo_count,
        memos=memos,
    )


@router.get("", response_model=list[HarvestRecordOut])
async def list_records(
    session: SessionDep,
    device: DeviceDep,
    plan_id: int | None = None,
    verified_only: bool = True,
) -> list[HarvestRecordOut]:
    q = (
        select(HarvestRecord)
        .where(HarvestRecord.device_id == device)
        .order_by(HarvestRecord.created_at.desc())
    )
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
