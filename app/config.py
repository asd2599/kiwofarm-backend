from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://kiwofarm:kiwofarm@localhost:5432/kiwofarm"

    openai_api_key: str = ""
    data_go_kr_key: str = ""
    nongsaro_api_key: str = ""
    ncpms_api_key: str = ""  # 국가농작물병해충관리시스템(ncpms.rda.go.kr) Open API 키
    kamis_cert_key: str = ""
    kamis_cert_id: str = ""
    kma_api_key: str = ""

    cors_origins: str = "http://localhost:3000"

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
