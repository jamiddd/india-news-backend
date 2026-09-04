from datetime import date, datetime
from typing import Optional, List, Any, Literal
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
    published_at: datetime
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    video_is_short: Optional[bool] = None
    video_duration_seconds: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class ArticleListOut(BaseModel):
    """Same as ArticleOut, except `content` here is a truncated preview
    (see CONTENT_PREVIEW_CHAR_LIMIT in main.py's _cluster_to_list_out), not
    the full scraped article body (which can run 3-8KB+ per article — full
    text still only ships via GET /clusters/{id} when a story is opened,
    see NewsViewModel.kt's selectCluster). The preview exists because the
    feed card (FeedNewsItemProduction.kt's "Cutout" layout) needs real
    prose to render a consistent ~6 lines; the AI cluster summary alone
    was too short/inconsistent for many stories. `author`/`media_type`
    aren't here either: confirmed unused by any screen, list or detail, so
    dropped from ArticleOut entirely rather than duplicated into two
    schemas."""
    id: int
    source_id: int
    source_name: str
    url: str
    title: str
    snippet: Optional[str] = None
    content: Optional[str] = None
    published_at: datetime
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    video_is_short: Optional[bool] = None
    video_duration_seconds: Optional[int] = None

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
    articles: List[ArticleOut] = []

    model_config = ConfigDict(from_attributes=True)


class StoryClusterListOut(BaseModel):
    """Same as StoryClusterOut minus `entities`/`topics`/`framing_comparison`
    — all three are detail-screen-only (StoryDetailScreen.kt), never read by
    any feed/list card. `ai_enriched` isn't here either: confirmed to not
    even be declared in the Android StoryCluster model, so dropped from
    StoryClusterOut entirely — it was sent on every cluster and deserialized
    never. See ArticleListOut for the matching per-article trim."""
    id: int
    headline: str
    summary: Optional[str] = None
    article_count: int
    first_seen_at: datetime
    last_updated_at: datetime
    articles: List[ArticleListOut] = []

    model_config = ConfigDict(from_attributes=True)


class PaginatedClustersOut(BaseModel):
    items: List[StoryClusterOut]
    next_cursor: Optional[str] = None
    has_more: bool


class PaginatedClustersListOut(BaseModel):
    """response_model for the slim list endpoints (GET /clusters, /search)
    — see StoryClusterListOut."""
    items: List[StoryClusterListOut]
    next_cursor: Optional[str] = None
    has_more: bool


class ClustersCacheEnvelope(BaseModel):
    """What GET /clusters actually caches — the page's clusters *before*
    the per-request weighted shuffle and explore-slot splice, which must
    run on every request (including cache hits) rather than being baked
    into a shared cached response. See main.py's list_story_clusters:
    `seed` and `user_id` are deliberately excluded from the cache key
    since neither affects which clusters this query returns, only how
    the cached page gets reordered/spliced per request. `weights` runs
    parallel to `items`, one _weighted_shuffle weight per cluster,
    computed once here so a cache hit doesn't need the ORM objects
    (headline_score, source boosts, explore_status) to reshuffle. Items are
    the slim StoryClusterListOut — this is what /clusters returns."""
    items: List[StoryClusterListOut]
    weights: List[float]
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
    # Independent of daily_notification_times_utc below — a user can have
    # both breaking alerts AND daily digests on at once. See
    # scripts/send_notifications.py for what each actually sends.
    breaking_notifications_enabled: bool = False
    # List of "HH:MM" (UTC) — one digest notification per entry, per day. The
    # client converts each local time-of-day pick to UTC before saving (see
    # NewsViewModel's preferred-time setter), so the backend never needs a
    # timezone field or per-user tz math. Empty list = no daily digests.
    daily_notification_times_utc: List[str] = Field(default_factory=list)
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


class DailyWordleOut(BaseModel):
    date: date
    answer: str
    word_length: int
    max_guesses: int
    accepted_guesses: List[str]


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
    # "read" (default, and what every pre-monetization client sends by
    # omitting the field), "framing_view" or "summary_expand". Only "read"
    # events feed user_entity_affinity — opening the framing panel says
    # something about interest, not about topic affinity.
    event_type: Literal["read", "framing_view", "summary_expand"] = "read"


class DonationLinkRequest(BaseModel):
    # Paise, matching Razorpay. Bounds are enforced again in
    # app/services/donations.py — this is the client-facing guard, that one is
    # the guard on the money.
    amount_paise: int = Field(ge=100, le=10_000_00)
    # The backend user id (usr_xxx), when the donor happens to be signed in.
    # Donating signed-out is expected, so this is optional and an unrecognised
    # id is dropped rather than rejected.
    user_id: Optional[str] = Field(default=None, max_length=64)


class DonationLinkResponse(BaseModel):
    url: str


class SaveStoryRequest(BaseModel):
    cluster_id: int


class ReportStoryRequest(BaseModel):
    reason: Literal["misleading", "factually_incorrect", "offensive", "duplicate_spam", "other"]
    note: Optional[str] = Field(default=None, max_length=1000)


class SavedStoryOut(BaseModel):
    saved_at: datetime
    cluster: StoryClusterOut


class SavedStoriesOut(BaseModel):
    items: List[SavedStoryOut]
    next_cursor: Optional[str] = None
    has_more: bool


class StarredSourcesOut(BaseModel):
    items: List[SourceOut]


class BlockedSourcesOut(BaseModel):
    items: List[SourceOut]


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
