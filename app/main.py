import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1 import (
    auth,
    community,
    crops,
    farmplan,
    garden,
    harvest,
    planting,
    recommend,
    rewards,
    twin,
)
from app.config import settings
from app.core.storage import UPLOAD_URL_PREFIX
from app.data import nongsaro_ebook
from app.db.session import init_db

logger = logging.getLogger(__name__)


async def _warm_crop_catalog() -> None:
    """작목 카탈로그(농사로 트리 순회 ~6s)를 백그라운드로 미리 채워 첫 검색을 빠르게.

    외부 API 실패해도 무시 — 캐시는 그대로 비고, 첫 실제 검색이 다시 시도한다.
    """
    try:
        items = await nongsaro_ebook.fetch_crop_catalog()
        logger.info("작목 카탈로그 워밍 완료: %d종", len(items))
    except Exception as e:  # noqa: BLE001 - 부팅을 막지 않도록 광범위 캐치
        logger.warning("작목 카탈로그 워밍 실패(첫 검색 시 재시도): %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 로컬 SQLite 테이블 생성(없을 때만). 임베딩은 DB 밖(로컬 파일 스토어)에 있다.
    await init_db()
    # 부팅을 막지 않도록 워밍은 백그라운드 태스크로.
    asyncio.create_task(_warm_crop_catalog())
    yield


app = FastAPI(title="KiwoFarm API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 업로드 파일(메모·수확 사진) 정적 서빙 — core/storage.file_url() 과 경로 일치.
_upload_root = Path(settings.upload_dir).resolve()
_upload_root.mkdir(parents=True, exist_ok=True)
app.mount(UPLOAD_URL_PREFIX, StaticFiles(directory=_upload_root), name="uploads")

app.include_router(auth.router, prefix="/api/v1")
app.include_router(recommend.router, prefix="/api/v1")
app.include_router(twin.router, prefix="/api/v1")
app.include_router(crops.router, prefix="/api/v1")
app.include_router(farmplan.router, prefix="/api/v1")
app.include_router(planting.router, prefix="/api/v1")
app.include_router(harvest.router, prefix="/api/v1")
app.include_router(rewards.router, prefix="/api/v1")
app.include_router(garden.router, prefix="/api/v1")
app.include_router(community.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
