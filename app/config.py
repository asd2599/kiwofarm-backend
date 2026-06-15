from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 로컬 기본값: SQLite 파일(서버 불필요). 운영은 .env 의 DATABASE_URL 로 postgres 지정.
    database_url: str = "sqlite+aiosqlite:///./data/kiwofarm.db"

    openai_api_key: str = ""
    # 농사로 통합 키 — 전 서비스(cropEbook·monthFd·fildMnfct·farmWorkingPlanNew 등)
    # 단일 신청. 구 KEY2/KEY3 체계는 2026-06-04 폐기.
    nongsaro_api_key: str = ""
    ncpms_api_key: str = ""  # 국가농작물병해충관리시스템(ncpms.rda.go.kr) Open API 키

    cors_origins: str = "http://localhost:3000"

    # 자체 인증(JWT HS256) 서명 시크릿 — 운영(fly)에서는 AUTH_SECRET 으로 교체할 것.
    auth_secret: str = "kiwofarm-beta-secret-change-in-prod"

    # 수확 인증 데모 모드: 멀티모달 판정과 무관하게 통과 처리(판정은 기록).
    # 6/30 시연 등 데모 환경에서만 true.
    harvest_demo_mode: bool = False

    # 사용자 업로드(메모·수확 사진) 로컬 저장 디렉터리 + 1파일 최대 크기(MB).
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 10

    @field_validator("database_url")
    @classmethod
    def _force_asyncpg(cls, v: str) -> str:
        # fly Postgres attach injects `postgres://...`; SQLAlchemy 2.x needs an
        # explicit driver, and the app uses asyncpg.
        if v.startswith("postgres://"):
            return "postgresql+asyncpg://" + v[len("postgres://") :]
        if v.startswith("postgresql://") and "+asyncpg" not in v.split("://", 1)[0]:
            return "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
