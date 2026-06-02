"""Async SQLAlchemy 엔진 / 세션 팩토리.

스크립트와 FastAPI 라우터에서 공통으로 쓴다. 로컬 개발은 SQLite(파일 1개,
서버 불필요), 운영은 postgres 로 DATABASE_URL 만 바꾸면 동일 코드로 동작한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    # sqlite:///./data/kiwofarm.db → 파일 부모 디렉터리 보장.
    _db_path = settings.database_url.split("///", 1)[-1]
    if _db_path and _db_path != ":memory:":
        Path(_db_path).resolve().parent.mkdir(parents=True, exist_ok=True)
    # aiosqlite 의 lock 대기 timeout(초). postgres(asyncpg)도 connect timeout 으로 통용.
    connect_args = {"timeout": 10}
else:
    # connect_args timeout: DB가 죽어 있을 때 60s+ 매달리지 않고 ~10s 안에
    # 명확히 실패시켜 (라우트가 500+CORS 헤더로 응답) "CORS 에러" 둔갑을 막는다.
    connect_args = {"timeout": 10}

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args=connect_args,
)


if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
        """WAL(동시 읽기/쓰기) + 외래키 강제(CASCADE 삭제 동작)."""
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    """모델 메타데이터로 테이블 생성(없을 때만). SQLite 로컬 개발용.

    postgres 운영에서는 alembic 마이그레이션을 쓰지만, create_all 은 이미 있는
    테이블을 건드리지 않으므로 양쪽에서 안전하다.
    """
    import app.db.models  # noqa: F401 - 모든 모델을 Base.metadata 에 등록
    from app.db.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 의존성. `Depends(get_session)` 형태로 사용."""
    async with async_session_factory() as session:
        yield session
