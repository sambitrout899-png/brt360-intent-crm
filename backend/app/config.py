from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    training_public_origin: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./brt360.db"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # The platform's own model on this server (deploy/local_llm.sh installs
    # Ollama with it). Tenants choose "local" in Admin -> AI Settings; where
    # it runs is decided here, by whoever runs the server.
    local_llm_base_url: str = "http://127.0.0.1:11434/v1"
    local_llm_model: str = "brt360-telecom"
    local_llm_timeout_seconds: float = 90.0
    # Provider for tenants that never chose one: external (if a key exists) / local / rules
    default_llm_provider: str = "external"

    # Comma-separated. Must include whatever origin the frontend is actually
    # served from - the browser enforces this, not a trust boundary on its own.
    cors_allowed_origins: str = "http://localhost:5173"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # Background jobs (app/scheduler.py). "embedded": one API worker runs them -
    # whichever takes the lock file first; "off": the API never does, a separate
    # `python -m app.scheduler` process runs them. Blank lock file = the temp dir.
    scheduler_mode: str = "embedded"
    scheduler_lock_file: str = ""

    # Postgres: the plain role the app's connections switch to (SET ROLE) so
    # row-level security applies to them - superusers bypass it (app/rls.py).
    # Empty = connect as the configured user. The Docker setup sets brt360_app.
    database_app_role: str = ""

    # Shared state for every worker (rate limits). Empty = each worker keeps
    # its own; the Docker setup points this at its Redis container.
    redis_url: str = ""

    # Services that run in their own container (app/services/), comma-separated:
    # the main API then leaves their routes out. Empty = everything in one app
    # (development, tests). The Docker setup sets "training".
    split_services: str = ""

    @property
    def split_services_list(self) -> list[str]:
        return [s.strip() for s in self.split_services.split(",") if s.strip()]

    # The AI gateway (app/services/ai_gateway.py). Set, every model call goes
    # through it and it alone holds the platform's model keys; empty = the app
    # calls the model itself (development, tests).
    ai_gateway_url: str = ""
    # Shared between the app and the gateway: only the platform's own services may use it.
    ai_gateway_token: str = ""
    # Model calls per tenant per minute through the gateway (0 = no limit).
    ai_gateway_tenant_rpm: int = 60
    # Tokens per tenant per calendar month through the gateway (0 = no budget);
    # once used up, the tenant's AI features use their rules until the 1st.
    ai_gateway_tenant_monthly_tokens: int = 0
    # Mask emails, phone numbers, card numbers and IBANs before a prompt goes to
    # an external model (app/services/ai_redact.py); the answer gets them back.
    ai_gateway_redact: bool = True

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

    # Notifications by email (app/crm_core/notification_delivery.py): "off" records
    # what would have been sent; "smtp" sends through this account.
    notify_email: str = "off"
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_ssl: bool = True  # 465 = SSL from the start; False = STARTTLS (usually port 587)
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "BRT 360 <no-reply@localhost>"

    # Sign-in lockout (app/crm_core/login_security.py): this many wrong
    # passwords or codes in a row lock the account for this many minutes.
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    # Sign-in attempts per client IP per minute, whatever the account.
    login_ip_per_minute: int = 20

    # RS256 signing (app/auth.py). With a private key file set, new tokens are
    # signed with it (header kid = the key's fingerprint) and other services can
    # verify them from /api/auth/jwks. Rotation: put the old key's PUBLIC pem in
    # previous files until its tokens expire (jwt_expiry_minutes). Shared-secret
    # (HS256) tokens are still accepted unless jwt_accept_hs256 is off - turn it
    # off once every session from before RS256 has expired.
    jwt_private_key_file: str = ""
    jwt_previous_public_key_files: str = ""
    jwt_accept_hs256: bool = True

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

    # The BRT_ID engine's secret key (app/brt_id.py). Every record's BRT_ID
    # is minted with it and incoming BRT_IDs (BIM loads) are verified with
    # it - keep it secret, keep it stable, and never change it on a live
    # database. The deploy script writes a random one into .env if missing.
    brt_id_secret: str = "brt360-dev-only-brt-id-secret"


@lru_cache
def get_settings() -> Settings:
    return Settings()
