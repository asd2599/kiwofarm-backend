"""데모 계정(device='demo') 캘린더 일지 시드.

시연용 상추 계획 1건을 보장하고, 그 일지를 파종→생육→수확 스토리(메모 7건,
사진 5장 — PIL 합성 생육 단계 이미지)로 교체한다. 재실행 시 메모를 전부
갈아끼우므로 idempotent.

실행:
    uv run python scripts/seed_demo_journal.py
"""

from __future__ import annotations

import asyncio
import io
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import DEMO_DEVICE_ID  # noqa: E402
from app.db.models.farm_plan import FarmPlan, FarmTask, MemoImage, TaskMemo  # noqa: E402
from app.db.session import async_session_factory, init_db  # noqa: E402

START = date(2026, 4, 1)

# (날짜, 메모, 사진 생육 단계 0~4 | None)
JOURNAL: list[tuple[date, str, int | None]] = [
    (date(2026, 4, 1), "상추 씨앗 파종. 줄뿌림 두 줄, 물 흠뻑.", 0),
    (date(2026, 4, 12), "떡잎이 일제히 올라왔다. 물은 이틀에 한 번.", 1),
    (date(2026, 4, 26), "본잎 4장. 웃자란 곳 위주로 솎아내기 1차.", None),
    (date(2026, 5, 9), "본잎 6~8장. 솎아낸 자리 안정적. 액비 1회.", 2),
    (date(2026, 5, 20), "진딧물 약간 발견 — 비눗물로 처리, 경과 관찰.", None),
    (date(2026, 5, 31), "잎이 손바닥만 하게 무성해졌다. 겉잎부터 수확 준비.", 3),
    (date(2026, 6, 3), "첫 수확! 겉잎 위주로 한 바구니. 쌈으로 저녁.", 4),
]

DEMO_TASKS: list[tuple[str, str, int, int]] = [
    # (title, category, day_offset, duration_days)
    ("씨앗 파종", "seeding", 0, 1),
    ("솎아내기 1차", "growing", 25, 1),
    ("웃거름(액비)", "fertilize", 38, 1),
    ("병해충 점검", "pest", 49, 1),
    ("겉잎 수확 시작", "harvest", 60, 7),
]


def _png(stage: int) -> bytes:
    """생육 단계(0=파종 직후 ~ 4=수확)별 합성 이미지."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (512, 512), (205, 185, 155))  # 흙 배경
    d = ImageDraw.Draw(img)
    size = 50 + stage * 80
    cx, cy = 256, 310
    for i in range(2 + stage):
        off = (i - (1 + stage) / 2) * (size // 3)
        d.ellipse(
            [cx + off - size // 2, cy - size, cx + off + size // 2, cy],
            fill=(60 + i * 12, 140 + stage * 22, 55),
        )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def main() -> None:
    await init_db()
    async with async_session_factory() as s:
        plan = await s.scalar(
            select(FarmPlan).where(
                FarmPlan.device_id == DEMO_DEVICE_ID, FarmPlan.crop_name == "상추"
            )
        )
        if plan is None:
            plan = FarmPlan(
                device_id=DEMO_DEVICE_ID,
                start_date=START,
                crop_item_code="lettuce",
                crop_kind_code="lettuce",
                crop_name="상추",
                region="서울 송파구",
                area=2.0,
                area_unit="pyeong",
            )
            s.add(plan)
            await s.flush()
            print(f"데모 상추 계획 생성 (id={plan.id})")
        else:
            print(f"데모 상추 계획 재사용 (id={plan.id})")

        # 작업도 표준 데모 세트로 교체 (재실행·재사용 모두 일관된 캘린더)
        old_tasks = (
            await s.scalars(select(FarmTask).where(FarmTask.plan_id == plan.id))
        ).all()
        for t in old_tasks:
            await s.delete(t)
        await s.flush()
        s.add_all(
            FarmTask(
                plan_id=plan.id, title=t, category=c, day_offset=o,
                duration_days=dur, order=i,
            )
            for i, (t, c, o, dur) in enumerate(DEMO_TASKS)
        )

        # 일지 교체 — 기존 메모(사진 포함 CASCADE) 삭제 후 스토리 주입
        old = (
            await s.scalars(select(TaskMemo).where(TaskMemo.plan_id == plan.id))
        ).all()
        for m in old:
            await s.delete(m)
        await s.flush()

        photos = 0
        for d, content, stage in JOURNAL:
            memo = TaskMemo(plan_id=plan.id, memo_date=d, content=content)
            s.add(memo)
            await s.flush()
            if stage is not None:
                data = _png(stage)
                s.add(
                    MemoImage(
                        memo_id=memo.id,
                        data=data,
                        original_name=f"lettuce_{d.isoformat()}.png",
                        content_type="image/png",
                        size_bytes=len(data),
                    )
                )
                photos += 1
        await s.commit()
        print(f"일지 시드: 메모 {len(JOURNAL)}건 · 사진 {photos}장 (기존 {len(old)}건 교체)")
        print(f"시연 진입: http://localhost:3000/calendar?planId={plan.id}&device=demo")


if __name__ == "__main__":
    asyncio.run(main())
