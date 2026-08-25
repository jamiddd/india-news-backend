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


class RelatedClustersOut(BaseModel):
    items: List[StoryClusterOut]
    # Display name of the anchor entity the grouping was found through (e.g.
    # "Govinda") — None if no related stories were found. Debug/UI hint only.
    actor: Optional[str] = None


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
    # Source.id (as a string key, since JSON object keys are always strings)
    # -> boost multiplier applied to headline_score in the "All Stories" feed
    # only (see GET /clusters's source_weights query param). A source absent
    # from this map gets the implicit default multiplier of 1.0 — this map
    # only needs to hold the sources a user has actually boosted.
    source_weights: dict[str, float] = Field(default_factory=dict)


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


class DailySudokuOut(BaseModel):
    date: date
    puzzle: List[int]
    solution: List[int]


class DailyWordSearchOut(BaseModel):
    date: date
    theme: str
    size: int
    rows: List[str]
    words: List[str]


class DailySpellingBeeOut(BaseModel):
    date: date
    letters: List[str]
    center_letter: str
    words: List[str]


class DailyWordLadderOut(BaseModel):
    date: date
    start_word: str
    target_word: str
    allowed_words: List[str]
    optimal_steps: int


class DailyQuizQuestionOut(BaseModel):
    id: int
    question: str
    options: List[str]
    correct_index: int
    explanation: str


class DailyQuizOut(BaseModel):
    date: date
    questions: List[DailyQuizQuestionOut]


class WordOfTheDayOut(BaseModel):
    date: date
    word: str
    pronunciation: str
    part_of_speech: str
    definition: str
    example: str
    origin: str


class BackgroundImageOut(BaseModel):
    url: str
    photographer: str
    photographer_url: str
    unsplash_url: str


class QuoteOfTheDayOut(BaseModel):
    date: date
    quote: str
    author: str
    background_image: Optional[BackgroundImageOut] = None


class HistoricalEventOut(BaseModel):
    year: int
    text: str
    article_url: Optional[str] = None


class OnThisDayOut(BaseModel):
    date: date
    events: List[HistoricalEventOut]
    attribution: str


class HoroscopeThemesOut(BaseModel):
    general: str
    career: str
    finance: str
    health: str
    romance: str


class DailyHoroscopeOut(BaseModel):
    date: date
    sign: str
    symbol: str
    element: str
    color: str
    color_hex: Optional[str] = None
    compatibility: List[str]
    lucky_number: int
    lucky_time: str
    mood: str
    horoscope: HoroscopeThemesOut
    scores: dict[str, int]


class PollOptionOut(BaseModel):
    id: int
    text: str
    votes: Optional[int] = None
    percentage: Optional[float] = None


class DailyPollOut(BaseModel):
    id: int
    date: date
    question: str
    context: str
    source_cluster_id: Optional[int] = None
    source_headline: Optional[str] = None
    closes_at: datetime
    options: List[PollOptionOut]
    selected_option_id: Optional[int] = None
    total_votes: Optional[int] = None


class PollVoteRequest(BaseModel):
    option_id: int


VALID_GAME_TYPES = {"crossword", "sudoku", "word_search", "spelling_bee", "word_ladder", "daily_quiz"}


class GameSessionRequest(BaseModel):
    puzzle_date: date
    score: Optional[int] = None
    completion_time_seconds: Optional[int] = None
    difficulty: Optional[str] = None


class GameTypeStatsOut(BaseModel):
    played: int
    completed: int
    attempted_incomplete: int
    best_score: Optional[int] = None
    avg_completion_time_seconds: Optional[int] = None
    last_played_date: Optional[date] = None


class ReadEventRequest(BaseModel):
    # Client-generated once per story view, reused across the "open" call
    # (fired on entry, dwell_ms/scroll_depth_pct omitted) and the "close"
    # call (fired on exit, both populated) so the server can upsert one row
    # per view instead of the two calls creating two rows. See
    # POST /users/{user_id}/read-events and app.models.ReadEvent.
    event_id: str = Field(max_length=64)
    cluster_id: int
    dwell_ms: Optional[int] = Field(default=None, ge=0)
    scroll_depth_pct: Optional[int] = Field(default=None, ge=0, le=100)


class GameStatsOut(BaseModel):
    total_played: int
    total_completed: int
    most_played_game: Optional[str] = None
    current_streak_days: int = 0
    longest_streak_days: int = 0
    level: int = 1
    xp: int = 0
    xp_to_next_level: int = 100
    by_game: dict[str, GameTypeStatsOut]
