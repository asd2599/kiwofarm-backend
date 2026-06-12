"""테스트용 계정 3개 생성 + 각 10000팜 지급(멱등). 운영 DB에 직접 쓴다.

실행: (backend 디렉터리에서) uv run python scripts/seed_test_accounts.py
제거: PointLedger reason='test_seed' + AppUser username LIKE 'testfarm%' 삭제.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.auth import hash_password
from app.db.models.point import PointLedger
from app.db.models.user import AppUser
from app.db.session import async_session_factory

ACCOUNTS = [
    ("testfarm1", "test1234", "테스트팜1"),
    ("testfarm2", "test1234", "테스트팜2"),
    ("testfarm3", "test1234", "테스트팜3"),
]
GRANT = 10000
REASON = "test_seed"


async def main() -> None:
    async with async_session_factory() as s:
        for username, password, nickname in ACCOUNTS:
            user = await s.scalar(select(AppUser).where(AppUser.username == username))
            if user is None:
                user = AppUser(
                    username=username,
                    password_hash=hash_password(password),
                    nickname=nickname,
                    address_sigungu=None,
                )
                s.add(user)
                await s.commit()
                await s.refresh(user)
                state = "created"
            else:
                state = "exists"
            device = f"user:{user.id}"
            dup = await s.scalar(
                select(PointLedger.id)
                .where(PointLedger.device_id == device, PointLedger.reason == REASON)
                .limit(1)
            )
            if dup is None:
                s.add(PointLedger(device_id=device, amount=GRANT, reason=REASON))
                await s.commit()
                grant = f"+{GRANT}팜"
            else:
                grant = "이미 지급됨"
            print(f"  {username} / {password}  (id={user.id}, {state}, {grant})")


if __name__ == "__main__":
    asyncio.run(main())
