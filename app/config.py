from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "India News App Backend"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://news_user:news_password@localhost:5432/news_db"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Ingestion
    DEFAULT_POLL_INTERVAL_SECONDS: int = 300  # 5 minutes
    INGESTION_USER_AGENT: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36 (IndiaNewsApp Engine)"

    # AI Enrichment
    ANTHROPIC_API_KEY: Optional[str] = None

    # API version negotiation — the client sends its own versionCode (see
    # BuildConfig/app/build.gradle.kts's defaultConfig.versionCode) as the
    # X-Client-Version header on every request (see main.py's
    # enforce_min_client_version middleware). Requests from a client older
    # than this get a 426 Upgrade Required instead of whatever confusing
    # downstream error an incompatible response shape would otherwise cause.
    # Defaults to 1 (the current versionCode) so this is a no-op until a
    # real breaking change ships and this is deliberately bumped alongside
    # it — raise via env var, no code change needed for a routine bump.
    MIN_SUPPORTED_APP_VERSION_CODE: int = 1

    # Error tracking (Sentry). Optional — sentry_sdk.init() is only called
    # in main.py when this is set, so local dev / CI without a DSN
    # configured behaves exactly as before (no-op, not a startup failure).
    SENTRY_DSN: Optional[str] = None

    # Deprecated: both email/password and Google Sign-In now go through
    # Firebase Authentication (see FIREBASE_CREDENTIALS_PATH below), which
    # verifies both under one token audience. Left in place, unused, in case
    # anything still references it — safe to remove in a later pass.
    GOOGLE_OAUTH_CLIENT_ID: Optional[str] = None

    # Path to the Firebase Admin SDK service-account JSON, used to verify
    # Firebase ID tokens sent by the client (both email/password and Google
    # Sign-In go through Firebase now). Login is rejected with a 500 until
    # this points at a real, readable file. Never commit this file — it's
    # bind-mounted onto the droplet, not baked into the image.
    FIREBASE_CREDENTIALS_PATH: Optional[str] = None

    # extra="ignore": .env / the container environment may carry vars that
    # aren't app settings at all (e.g. POSTGRES_PASSWORD, which docker-compose
    # only uses to interpolate DATABASE_URL) — don't fail startup over those.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
