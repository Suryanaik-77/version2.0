from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────
    APP_NAME: str = "VLSI Interview Platform"
    DEBUG: bool = False
    ENVIRONMENT: str = "production"

    # ── Redis ────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_POOL_MIN_SIZE: int = 10
    REDIS_POOL_MAX_SIZE: int = 50
    # Separate pool for pub/sub (blocking connections must not share pool)
    REDIS_PUBSUB_URL: str = "redis://localhost:6379/0"

    # ── PostgreSQL ───────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://vlsi:vlsi@localhost:5432/vlsi_interview"

    # ── Auth ─────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = "change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Session TTLs (seconds) ───────────────────────────────────────
    SESSION_TTL: int = 14400           # 4 hours active session
    RECONNECT_WINDOW: int = 30         # WS reconnect grace period
    HEARTBEAT_INTERVAL: int = 15       # Client heartbeat frequency
    HEARTBEAT_STALE_AFTER: int = 45    # Mark stale after this many seconds

    # ── Latency targets (milliseconds) ──────────────────────────────
    # These are enforced via timeout parameters — not just documentation.
    STT_TIMEOUT_MS: int = 700
    FIRST_TOKEN_DEADLINE_MS: int = 400
    FIRST_AUDIO_DEADLINE_MS: int = 1200
    TURN_TOTAL_DEADLINE_MS: int = 5000
    EVAL_ASYNC_DEADLINE_MS: int = 8000

    # ── Rate limiting ────────────────────────────────────────────────
    RATE_LIMIT_RPM: int = 120          # requests per minute per IP
    RATE_LIMIT_SESSION_RPM: int = 60   # requests per minute per session

    # ── Provider (Phase 2/3 — declared here for config completeness) ─
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    MISTRAL_API_KEY: str = ""
    INWORLD_API_KEY: str = ""

    # V2 product fields
    DOMAIN: str = "localhost"
    ALLOW_SELF_REGISTER_REVIEWER: bool = False

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
