from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: list[int] = Field(default_factory=list)

    database_url: str = "postgresql+asyncpg://bot:bot@localhost:5432/bot"

    datanewton_api_key: str = ""
    checko_api_key: str = ""
    primary_provider: str = "fake"

    # веб-разведка через Claude; пусто — функция выключена
    anthropic_api_key: str = ""
    research_model: str = "claude-sonnet-5"

    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, v):
        # pydantic-settings пытается разобрать значение как JSON до валидатора,
        # поэтому "5731136459" приезжает сюда уже int-ом
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            return [int(x) for x in v.replace(" ", "").split(",") if x]
        return v

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_dsn(cls, v: str) -> str:
        # Railway отдаёт postgresql:// — SQLAlchemy async нужен драйвер asyncpg
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v


settings = Settings()
