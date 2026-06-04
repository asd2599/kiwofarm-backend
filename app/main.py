from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import crops, farmplan, recommend, sales, support, twin
from app.config import settings
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 로컬 SQLite 테이블 생성(없을 때만). 임베딩은 DB 밖(로컬 파일 스토어)에 있다.
    await init_db()
    yield


app = FastAPI(title="KiwoFarm API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(recommend.router, prefix="/api/v1")
app.include_router(twin.router, prefix="/api/v1")
app.include_router(crops.router, prefix="/api/v1")
app.include_router(sales.router, prefix="/api/v1")
app.include_router(support.router, prefix="/api/v1")
app.include_router(farmplan.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
