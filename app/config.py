from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://kiwofarm:kiwofarm@localhost:5432/kiwofarm"

    openai_api_key: str = ""
    data_go_kr_key: str = ""
    kamis_cert_key: str = ""
    kamis_cert_id: str = ""
    kma_api_key: str = ""

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
