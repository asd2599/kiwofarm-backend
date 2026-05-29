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

engine = create_async_engine(settings.database_url, pool_pre_ping=True)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성. `Depends(get_session)` 형태로 사용."""
    async with async_session_factory() as session:
        yield session
