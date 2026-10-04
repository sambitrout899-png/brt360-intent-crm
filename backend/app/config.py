from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./brt360.db"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    # Comma-separated. Must include whatever origin the frontend is actually
    # served from - the browser enforces this, not a trust boundary on its own.
    cors_allowed_origins: str = "http://localhost:5173"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # Confidence below this routes the plan to a human instead of auto-executing,
    # per HLD 4.3 / 8.5 (AI Governance).
    auto_execute_confidence_threshold: float = 0.75


@lru_cache
def get_settings() -> Settings:
    return Settings()
