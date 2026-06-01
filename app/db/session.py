"""Async SQLAlchemy 엔진 / 세션 팩토리.

스크립트와 FastAPI 라우터에서 공통으로 쓴다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

# connect_args timeout: DB가 죽어 있을 때 60s+ 매달리지 않고 ~10s 안에
# 명확히 실패시켜 (라우트가 500+CORS 헤더로 응답) "CORS 에러" 둔갑을 막는다.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"timeout": 10},
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성. `Depends(get_session)` 형태로 사용."""
    async with async_session_factory() as session:
        yield session
