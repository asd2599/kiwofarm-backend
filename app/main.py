from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import crops, farmplan, recommend, shipping, twin
from app.config import settings

app = FastAPI(title="KiwoFarm API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(shipping.router, prefix="/api/v1")
app.include_router(recommend.router, prefix="/api/v1")
app.include_router(twin.router, prefix="/api/v1")
app.include_router(crops.router, prefix="/api/v1")
app.include_router(farmplan.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
