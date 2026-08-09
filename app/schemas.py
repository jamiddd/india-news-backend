from datetime import date, datetime
from typing import Optional, List, Any
from pydantic import BaseModel, ConfigDict, Field

class SourceOut(BaseModel):
    id: int
    name: str
    slug: str
    feed_url: str
    homepage_url: Optional[str] = None
    language: str
    category: str
    region: str
    status: str

    model_config = ConfigDict(from_attributes=True)


class ArticleOut(BaseModel):
    id: int
    source_id: int
    source_name: str
    url: str
    title: str
    snippet: Optional[str] = None
    content: Optional[str] = None
    author: Optional[str] = None
    published_at: datetime
    image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class StoryClusterOut(BaseModel):
    id: int
    headline: str
    summary: Optional[str] = None
    article_count: int
    first_seen_at: datetime
    last_updated_at: datetime
    entities: Optional[Any] = None
    topics: Optional[Any] = None
    framing_comparison: Optional[Any] = None
    # True only on a confirmed successful Anthropic API call — NOT implied
    # by entities/topics/framing_comparison being present, since those are
    # always populated by the free rule-based fallback first regardless of
    # whether the paid API call succeeds. See StoryCluster.ai_enriched.
    ai_enriched: bool = False
    articles: List[ArticleOut] = []

    model_config = ConfigDict(from_attributes=True)


class PaginatedClustersOut(BaseModel):
    items: List[StoryClusterOut]
    next_cursor: Optional[str] = None
    has_more: bool


class UserPreferences(BaseModel):
    theme_mode: str = "system"
    accent_color: str = "blue"
    language_pref: str = "all"
    enabled_categories: List[str] = Field(default_factory=list)
    custom_categories: List[str] = Field(default_factory=list)
    # "off" | "daily" | "breaking" — see scripts/send_notifications.py for
    # what each mode actually sends.
    notification_frequency: str = "off"
    # HH:MM, UTC — the client converts the user's local time-of-day pick to
    # UTC before saving (see NewsViewModel's preferred-time setter), so the
    # backend never needs a timezone field or per-user tz math. Only
    # meaningful when notification_frequency == "daily".
    notification_time_utc: Optional[str] = None


class DeviceTokenRegisterRequest(BaseModel):
    fcm_token: str
    platform: str = "android"


class UserAuthRequest(BaseModel):
    email: str
    display_name: str
    provider: str
    uid: Optional[str] = None


class UserAuthResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    token: Optional[str] = None
    preferences: UserPreferences


class AccountDeleteRequest(BaseModel):
    uid: str  # Firebase ID token, verified server-side — same convention as UserAuthRequest.uid


class CrosswordClueOut(BaseModel):
    number: int
    direction: str
    row: int
    col: int
    length: int
    clue: str


class DailyCrosswordOut(BaseModel):
    date: date
    size: int
    rows: List[str]
    clues: List[CrosswordClueOut]


class CrosswordCellInput(BaseModel):
    row: int = Field(ge=0, le=10)
    col: int = Field(ge=0, le=10)
    letter: str = Field(min_length=1, max_length=1)


class CrosswordCheckRequest(BaseModel):
    date: date
    cells: List[CrosswordCellInput] = Field(default_factory=list)
    scope: str = "grid"
    clue_number: Optional[int] = None
    clue_direction: Optional[str] = None


class CrosswordCheckResponse(BaseModel):
    incorrect_cells: List[List[int]]
    complete: bool


class CrosswordRevealRequest(BaseModel):
    date: date
    row: int = Field(ge=0, le=10)
    col: int = Field(ge=0, le=10)


class CrosswordRevealResponse(BaseModel):
    row: int
    col: int
    letter: str
