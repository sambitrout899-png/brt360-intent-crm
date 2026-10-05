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

    # JWT auth. The default is only safe for local dev (a fresh process
    # restart invalidates every outstanding token, which is fine there) -
    # production MUST set a real JWT_SECRET_KEY in .env, or every token
    # signed before a restart silently becomes unverifiable, and worse,
    # a predictable default would let anyone forge tokens.
    jwt_secret_key: str = "dev-only-insecure-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 480

    # bcrypt's cost factor. 12 (the library default) is the right call for
    # real production passwords - but it's ~0.3s per hash, and the test
    # suite creates several users per test (every fixture + every
    # multi-tenant test), which adds up fast purely from hashing, not from
    # anything the tests are actually verifying. tests/conftest.py lowers
    # this via the BCRYPT_ROUNDS env var before the app is ever imported;
    # production should never set that env var.
    bcrypt_rounds: int = 12

    # Bootstrap credentials app.seed uses to create the first platform_admin
    # and the "default" tenant's first admin, if neither already exists.
    # The defaults are fine for local dev; set real values in .env before
    # seeding a production database, and change the password after first
    # login either way.
    bootstrap_platform_admin_email: str = "platform-admin@brt360.local"
    bootstrap_platform_admin_password: str = "change-me-now"
    bootstrap_tenant_admin_email: str = "admin@default.local"
    bootstrap_tenant_admin_password: str = "change-me-now"


@lru_cache
def get_settings() -> Settings:
    return Settings()
