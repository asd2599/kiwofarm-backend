"""도감 데모용 수확 인증 기록 시드.

시연·개발 화면 확인용으로 verified 수확 기록을 넣는다 (사진 파일은 없음 —
도감 화면은 사진을 쓰지 않는다). 재실행 시 기존 데모 기록(reason='demo seed')을
지우고 다시 넣는다 (idempotent).

실행:
    uv run python scripts/seed_demo_harvest.py          # 시드 주입
    uv run python scripts/seed_demo_harvest.py --clear  # 데모 기록 삭제만
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import delete

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import DEMO_DEVICE_ID  # noqa: E402
from app.db.models.harvest import HarvestRecord  # noqa: E402
from app.db.session import async_session_factory, init_db  # noqa: E402

DEMO_REASON = "demo seed"

# (slug, 이름, 며칠 전, 신선도, 수량)
DEMO_ROWS = [
    ("lettuce", "상추", 21, 5, "상추 약 15장"),
    ("lettuce", "상추", 7, 4, "상추 약 10장"),
    ("cherry_tomato", "방울토마토", 5, 5, "방울토마토 한 줌(15알)"),
    ("strawberry", "딸기", 2, 4, "딸기 8알"),
    ("basil", "바질", 1, 5, "바질 한 움큼"),
]


async def main() -> None:
    ap = argparse.ArgumentParser(description="도감 데모 수확 기록 시드")
    ap.add_argument("--clear", action="store_true", help="데모 기록 삭제만")
    args = ap.parse_args()

    await init_db()
    async with async_session_factory() as session:
        deleted = (
            await session.execute(
                delete(HarvestRecord).where(HarvestRecord.reason == DEMO_REASON)
            )
        ).rowcount
        if args.clear:
            await session.commit()
            print(f"데모 기록 {deleted}건 삭제")
            return

        today = date.today()
        for slug, name, days_ago, fresh, qty in DEMO_ROWS:
            session.add(
                HarvestRecord(
                    device_id=DEMO_DEVICE_ID,  # 시연 계정(?device=demo) 소유
                    plan_id=None,
                    crop_slug=slug,
                    crop_name=name,
                    photo_path=None,
                    verified=True,
                    confidence=0.95,
                    verdict={
                        "crop_match": True,
                        "is_harvest": True,
                        "freshness": fresh,
                        "quantity": qty,
                        "fake_suspect": False,
                        "demo_mode": True,
                    },
                    reason=DEMO_REASON,
                    harvested_at=today - timedelta(days=days_ago),
                )
            )
        await session.commit()
        print(f"데모 기록 {deleted}건 교체 → {len(DEMO_ROWS)}건 주입")


if __name__ == "__main__":
    asyncio.run(main())
