from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 로컬 기본값: SQLite 파일(서버 불필요). 운영은 .env 의 DATABASE_URL 로 postgres 지정.
    database_url: str = "sqlite+aiosqlite:///./data/kiwofarm.db"

    openai_api_key: str = ""
    data_go_kr_key: str = ""
    nongsaro_api_key: str = ""  # 농사로 cropEbook((신)작목별 농업기술정보)용
    # 농사로 주간농사정보/이달의 농업기술/월간농업기술정보 신청 키(cropEbook 키와 별개).
    nongsaro_api_key2: str = ""
    ncpms_api_key: str = ""  # 국가농작물병해충관리시스템(ncpms.rda.go.kr) Open API 키
    kamis_cert_key: str = ""
    kamis_cert_id: str = ""
    kma_api_key: str = ""

    cors_origins: str = "http://localhost:3000"

    # 사용자 업로드(메모 사진 등) 로컬 저장 디렉터리 + 1파일 최대 크기(MB).
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
