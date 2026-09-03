from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "India News App Backend"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://news_user:news_password@localhost:5432/news_db"

    # Connection pool sizing. Configurable because the safe value depends on
    # deployment topology, which the code cannot see — see app/database.py
    # for the arithmetic. Defaults are sized for the current production
    # layout (2 droplets x 4 engines against a 15-client Supabase session
    # pooler), NOT for SQLAlchemy's defaults of 5 + 10, which oversubscribe
    # that ceiling eightfold.
    DB_POOL_SIZE: int = 2
    DB_MAX_OVERFLOW: int = 0
    DB_POOL_TIMEOUT: int = 30

    # Set true when DATABASE_URL points at a TRANSACTION-mode pooler
    # (Supabase port 6543) rather than session mode (6543's session
    # equivalent) or a direct connection (5432). Transaction mode hands each
    # transaction a different backend, which breaks server-side prepared
    # statements — see app/database.py for what this switches on. Wrong
    # value is not silently tolerated in either direction: true against a
    # direct connection merely costs a little per-query planning, false
    # against a transaction pooler raises DuplicatePreparedStatementError
    # under concurrency.
    DB_PGBOUNCER: bool = False
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    
    # Ingestion
    DEFAULT_POLL_INTERVAL_SECONDS: int = 300  # 5 minutes
    INGESTION_USER_AGENT: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0.0.0 Safari/537.36 (IndiaNewsApp Engine)"

    # Minimum distinct outlets a story needs before it counts as
    # corroborated. Today this only marks when a cluster crosses the line
    # (StoryCluster.became_multi_source_at, written in poller.py) — the feed
    # still shows everything. It becomes the actual feed gate in a later
    # step; kept in config from the start so the threshold can be tightened
    # without a code change if false merges show up at scale. See
    # docs/multi-source-feed-plan.md.
    FEED_MIN_DISTINCT_SOURCES: int = 2

    # AI Enrichment
    ANTHROPIC_API_KEY: Optional[str] = None

    # Unsplash — background photo for Quote of the Day. Free tier (50
    # req/hour on the "Demo" access tier), created at unsplash.com/developers.
    # Absent key = feature no-ops (app falls back to a plain gradient).
    UNSPLASH_ACCESS_KEY: Optional[str] = None

    # Daily sign-level horoscope provider. Kept server-side so the provider
    # can be changed or the feature disabled without releasing a new app.
    ASTROJSON_API_KEY: Optional[str] = None
    HOROSCOPE_ENABLED: bool = True

    # Daily games (crossword, sudoku, word search, spelling bee, word
    # ladder, quiz) content provider. Absent key = each game falls through
    # to its existing curated/algorithmic generator, same as before.
    APIVERVE_API_KEY: Optional[str] = None

    # Human-reviewed AI daily poll. All secrets are backend-only.
    POLL_ADMIN_USERNAME: str = "admin"
    POLL_ADMIN_PASSWORD: Optional[str] = None
    POLL_SESSION_SECRET: Optional[str] = None
    POLL_VOTER_HASH_SECRET: Optional[str] = None

    # When a fresh AI poll draft is generated (04:30 IST), an FCM push is
    # sent to every device registered to this account, deep-linking straight
    # to the admin review page so the 09:00 publish window isn't missed.
    # Absent email = feature no-ops (no admin account to notify).
    ADMIN_USER_EMAIL: Optional[str] = None
    ADMIN_POLL_REVIEW_URL: str = "https://openindiannews.com/admin/polls"

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
