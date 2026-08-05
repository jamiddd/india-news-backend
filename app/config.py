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

    # Auth — must match the Android app's Google Sign-In server/web client ID
    # (GetGoogleIdOption.setServerClientId(...) in LoginScreen.kt), since that's
    # the audience Google's ID tokens are issued for. Login with provider="google"
    # is rejected until this is set to a real value.
    GOOGLE_OAUTH_CLIENT_ID: Optional[str] = None

    # extra="ignore": .env / the container environment may carry vars that
    # aren't app settings at all (e.g. POSTGRES_PASSWORD, which docker-compose
    # only uses to interpolate DATABASE_URL) — don't fail startup over those.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
