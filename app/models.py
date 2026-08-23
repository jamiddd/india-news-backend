from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import (
    Column, Integer, String, Text, BigInteger, Date, DateTime, ForeignKey, Index, JSON, Boolean, Float, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.database import Base

def utc_now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True)
    email = Column(String(320), nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    provider = Column(String(50), nullable=False)
    provider_uid = Column(String(255), nullable=True)
    preferences = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    __table_args__ = (
        # Single-column, not composite with `provider`: Firebase Auth gives one
        # stable uid per account regardless of which linked provider (password
        # or Google) signed in, so provider_uid alone is the right identity key.
        # See backend/scripts/migrate_users_provider_uid_index.py for the
        # migration this required on an already-deployed database.
        Index("uq_users_provider_uid", "provider_uid", unique=True),
    )

    device_tokens = relationship("DeviceToken", cascade="all, delete-orphan")
    game_sessions = relationship("GameSession", cascade="all, delete-orphan")


class DeviceToken(Base):
    """An FCM registration token for one (user, device) pairing. A separate
    table rather than a column on User so one account can hold multiple
    devices/reinstalls without clobbering prior tokens, and a token can be
    looked up/deactivated independently (e.g. on an FCM
    UNREGISTERED/invalid-argument delivery error — see
    scripts/send_notifications.py). Unique on fcm_token alone, not
    (user_id, fcm_token): re-registering the same physical token under a
    different logged-in user (shared device, re-login) should reassign
    ownership via upsert, not create a duplicate row."""
    __tablename__ = "device_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    fcm_token = Column(String(512), nullable=False)
    platform = Column(String(20), default="android")
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    __table_args__ = (
        Index("uq_device_tokens_fcm_token", "fcm_token", unique=True),
    )


class NotificationLog(Base):
    """One row per push notification actually sent to a user for a given
    cluster. Serves two purposes for scripts/send_notifications.py: (1)
    dedup — never notify the same user about the same cluster twice, and
    (2) breaking-mode daily-cap enforcement — count today's rows for
    (user_id, mode='breaking') before sending another."""
    __tablename__ = "notification_log"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    cluster_id = Column(Integer, ForeignKey("story_clusters.id", ondelete="CASCADE"), nullable=False)
    mode = Column(String(20), nullable=False)  # "daily" | "breaking"
    sent_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    __table_args__ = (
        Index("idx_notiflog_user_sent", "user_id", "sent_at"),
        Index("idx_notiflog_user_cluster", "user_id", "cluster_id"),
    )


class GameSession(Base):
    """One row per (user, game_type, puzzle_date): tracks whether that day's
    puzzle was opened ("attempted") and/or finished ("completed"). Upserted
    on both /start and /complete rather than appended, so replaying the same
    day's puzzle doesn't inflate the "games played" count. game_type is a
    free-form string (not an FK) matching one of the Daily* puzzle tables'
    short names: "crossword" | "sudoku" | "word_search" | "spelling_bee" |
    "word_ladder" | "daily_quiz"."""
    __tablename__ = "game_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    game_type = Column(String(32), nullable=False)
    puzzle_date = Column(Date, nullable=False)
    completed = Column(Boolean, nullable=False, default=False)
    started_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("uq_game_sessions_user_game_date", "user_id", "game_type", "puzzle_date", unique=True),
    )


class DailyCrossword(Base):
    """One validated, shared crossword for an Asia/Kolkata calendar date."""
    __tablename__ = "daily_crosswords"

    id = Column(Integer, primary_key=True, index=True)
    puzzle_date = Column(Date, nullable=False, unique=True, index=True)
    size = Column(Integer, nullable=False, default=11)
    # Public layout contains only '#' and '.'; the solution remains separate
    # so GET /crossword/daily never exposes answers.
    grid = Column(JSON, nullable=False)
    clues = Column(JSON, nullable=False)
    solution = Column(JSON, nullable=False)
    source = Column(String(30), nullable=False, default="ai")
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class DailySudoku(Base):
    """One canonical Sudoku shared by every client for an India calendar date."""
    __tablename__ = "daily_sudokus"

    id = Column(Integer, primary_key=True, index=True)
    puzzle_date = Column(Date, nullable=False, unique=True, index=True)
    puzzle = Column(JSON, nullable=False)
    solution = Column(JSON, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class DailyWordSearch(Base):
    """One canonical themed word search for an India calendar date."""
    __tablename__ = "daily_word_searches"

    id = Column(Integer, primary_key=True, index=True)
    puzzle_date = Column(Date, nullable=False, unique=True, index=True)
    theme = Column(String(80), nullable=False)
    grid = Column(JSON, nullable=False)
    words = Column(JSON, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class DailySpellingBee(Base):
    __tablename__ = "daily_spelling_bees"
    id = Column(Integer, primary_key=True, index=True)
    puzzle_date = Column(Date, nullable=False, unique=True, index=True)
    letters = Column(JSON, nullable=False)
    center_letter = Column(String(1), nullable=False)
    words = Column(JSON, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class DailyWordLadder(Base):
    __tablename__ = "daily_word_ladders"
    id = Column(Integer, primary_key=True, index=True)
    puzzle_date = Column(Date, nullable=False, unique=True, index=True)
    start_word = Column(String(20), nullable=False)
    target_word = Column(String(20), nullable=False)
    allowed_words = Column(JSON, nullable=False)
    optimal_steps = Column(Integer, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class DailyQuiz(Base):
    __tablename__ = "daily_quizzes"
    id = Column(Integer, primary_key=True, index=True)
    puzzle_date = Column(Date, nullable=False, unique=True, index=True)
    questions = Column(JSON, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class DailyEditorial(Base):
    __tablename__ = "daily_editorial_features"
    id = Column(Integer, primary_key=True, index=True)
    feature_date = Column(Date, nullable=False, unique=True, index=True)
    word = Column(JSON, nullable=False)
    quote = Column(JSON, nullable=False)
    historical_events = Column(JSON, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class DailyHoroscope(Base):
    """Canonical provider response for one zodiac sign on one India date."""
    __tablename__ = "daily_horoscopes"
    id = Column(Integer, primary_key=True, index=True)
    forecast_date = Column(Date, nullable=False, index=True)
    sign = Column(String(20), nullable=False)
    forecast = Column(JSON, nullable=False)
    provider = Column(String(30), nullable=False, default="astrojson")
    generated_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    __table_args__ = (Index("uq_daily_horoscope_date_sign", "forecast_date", "sign", unique=True),)


class DailyPoll(Base):
    __tablename__ = "daily_polls"
    id = Column(Integer, primary_key=True)
    poll_date = Column(Date, nullable=False, unique=True, index=True)
    question = Column(Text, nullable=False)
    context = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="draft", index=True)
    source_cluster_id = Column(Integer, ForeignKey("story_clusters.id", ondelete="SET NULL"), nullable=True)
    source_headline = Column(Text, nullable=True)
    generation_method = Column(String(20), nullable=False, default="ai")
    publish_at = Column(DateTime(timezone=True), nullable=False)
    closes_at = Column(DateTime(timezone=True), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)


class PollOption(Base):
    __tablename__ = "poll_options"
    id = Column(Integer, primary_key=True)
    poll_id = Column(Integer, ForeignKey("daily_polls.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("poll_id", "position", name="uq_poll_option_position"),)


class PollVote(Base):
    __tablename__ = "poll_votes"
    id = Column(Integer, primary_key=True)
    poll_id = Column(Integer, ForeignKey("daily_polls.id", ondelete="CASCADE"), nullable=False, index=True)
    option_id = Column(Integer, ForeignKey("poll_options.id", ondelete="CASCADE"), nullable=False, index=True)
    voter_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    __table_args__ = (UniqueConstraint("poll_id", "voter_hash", name="uq_poll_vote_voter"),)


class PollFallback(Base):
    __tablename__ = "poll_fallbacks"
    id = Column(Integer, primary_key=True)
    question = Column(Text, nullable=False)
    context = Column(Text, nullable=False)
    options = Column(JSON, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(255), unique=True, index=True, nullable=False)
    feed_url = Column(Text, nullable=False, unique=True)
    homepage_url = Column(Text, nullable=True)
    language = Column(String(10), default="en")
    category = Column(String(50), default="general")
    region = Column(String(50), default="national")
    
    poll_interval_seconds = Column(Integer, default=300)
    etag = Column(String(255), nullable=True)
    last_modified = Column(String(255), nullable=True)
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    consecutive_failures = Column(Integer, default=0)
    status = Column(String(50), default="active")  # active, degraded, disabled

    articles = relationship("Article", back_populates="source", cascade="all, delete-orphan")


class StoryCluster(Base):
    __tablename__ = "story_clusters"

    id = Column(Integer, primary_key=True, index=True)
    representative_article_id = Column(Integer, ForeignKey("articles.id", ondelete="SET NULL"), nullable=True)
    headline = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    article_count = Column(Integer, default=1)

    # Distinct Source.id count among this cluster's articles — NOT the same
    # as article_count, which isn't source-deduped (two articles from the
    # same outlet both increment it). Maintained incrementally in
    # poller.py's matching loop. The real signal for "how independently
    # corroborated is this story", used to rank the default "All Stories"
    # feed by importance rather than raw recency.
    distinct_source_count = Column(Integer, default=1)
    # Precomputed ranking score (distinct_source_count decayed by recency,
    # HN-style) — recomputed in bulk once per poll cycle in
    # poll_all_sources(). Stored rather than computed at query time so the
    # "All Stories" feed can keyset-paginate against an index instead of
    # re-aggregating on every request. See app/services/poller.py.
    headline_score = Column(Float, default=0.0)

    first_seen_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    last_updated_at = Column(DateTime(timezone=True), default=utc_now, index=True)

    entities = Column(JSON, nullable=True)  # {"persons": [], "organizations": [], "locations": []}
    topics = Column(JSON, nullable=True)    # ["politics", "economy"]
    framing_comparison = Column(JSON, nullable=True) # Outlet headline angle comparison

    # Shadow signal for the feed ranking redesign (piece 1: global
    # importance) — the reactivation ratio of this cluster's single most
    # "spiking" entity, per app.services.entity_graph / recomputed
    # alongside headline_score in app.services.poller.poll_all_sources().
    # NOT wired into /clusters ordering yet; written for offline validation
    # only. See the "Feed ranking redesign" design memory.
    entity_boost = Column(Float, default=0.0, nullable=False)

    # Feed ranking redesign, piece 3 (explore-slot bandit): lifecycle of
    # this cluster as an explore candidate. "pending" = not yet decided
    # (still collecting exposures, or not a candidate at all — most
    # clusters stay "pending" forever); "promoted" = cleared the engagement
    # bar and gets a real, live ranking boost (see EXPLORE_PROMOTED_BOOST in
    # app.services.explore_bandit and its use in /clusters' effective_score);
    # "rejected" = collected enough exposures without clearing the bar,
    # excluded from future candidate selection so the pool keeps moving.
    # Recomputed in poller.py alongside entity_stats/headline_score.
    explore_status = Column(String(16), default="pending", nullable=False)

    # True only when the Anthropic API call in enrich_cluster_with_ai()
    # actually succeeded — NOT when entities/topics/framing_comparison are
    # merely non-null. Those three are always populated by the free
    # rule-based baseline first, unconditionally, before the paid API call
    # is even attempted — so a cluster can have "enrichment" data on it
    # purely from the fallback (e.g. because the Anthropic key ran out of
    # credit) while this stays False. See app/services/enrichment.py and
    # scripts/enrich_all_clusters.py, which targets this column (not
    # entities.is_(None)) to find clusters that still need a real AI pass.
    ai_enriched = Column(Boolean, default=False, nullable=False, index=True)

    articles = relationship("Article", back_populates="cluster", foreign_keys="Article.cluster_id")


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True)
    
    url = Column(Text, nullable=False)
    url_hash = Column(String(64), unique=True, index=True, nullable=False)  # SHA-256 canonical hash
    
    title = Column(Text, nullable=False)
    snippet = Column(Text, nullable=True)
    content = Column(Text, nullable=True)  # Full article body, scraped from the article URL
    author = Column(String(255), nullable=True)
    
    published_at = Column(DateTime(timezone=True), nullable=False, index=True)
    fetched_at = Column(DateTime(timezone=True), default=utc_now)
    
    image_url = Column(Text, nullable=True)
    categories = Column(JSON, nullable=True)
    
    simhash = Column(BigInteger, nullable=True, index=True)
    cluster_id = Column(Integer, ForeignKey("story_clusters.id", ondelete="SET NULL"), nullable=True, index=True)

    source = relationship("Source", back_populates="articles")
    cluster = relationship("StoryCluster", back_populates="articles", foreign_keys=[cluster_id])


class ReadEvent(Base):
    """
    Feed ranking redesign, piece 2 (per-user affinity / "For You" tab): one
    row per (user, cluster) story view. The client sends an `event_id` it
    generates once per view; the first call (on open) inserts this row with
    dwell_ms/scroll_depth_pct null, and a later call from the same view (on
    close) updates those two columns in place — see
    POST /users/{user_id}/read-events in app/main.py. dwell_ms/scroll_depth
    together (not raw opens) drive app.services.affinity's engagement
    weighting; also the source data for piece 3's explore-slot engagement
    scoring once that's built.
    """
    __tablename__ = "read_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    cluster_id = Column(Integer, ForeignKey("story_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    event_id = Column(String(64), nullable=False)
    opened_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    dwell_ms = Column(Integer, nullable=True)
    scroll_depth_pct = Column(Integer, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("uq_read_events_user_event", "user_id", "event_id", unique=True),
    )


class UserEntityAffinity(Base):
    """
    Feed ranking redesign, piece 2: per-user mirror of EntityStat — how much
    a given user's own reads have engaged with a canonical entity (see
    app.services.entity_graph.canonicalize_entity), decayed on a much
    shorter half-life than the global entity_stats (personal interest drifts
    faster than global newsworthiness). Updated in
    app.services.affinity.record_engagement() when a read_events row gets
    its dwell/scroll close-out. Powers GET /clusters/for-you's ranking.
    """
    __tablename__ = "user_entity_affinity"

    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    entity_key = Column(String(255), primary_key=True)
    affinity_decayed = Column(Float, default=0.0, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ExploreExposure(Base):
    """
    Feed ranking redesign, piece 3 (explore-slot bandit): one row logged
    every time a candidate cluster is spliced into a logged-in user's "All
    Stories" first page (see GET /clusters' optional user_id param and
    app.services.explore_bandit.pick_candidate). Later joined against
    ReadEvent (same user_id + cluster_id, opened after exposed_at) to score
    engagement — a user who never opened it scores 0 engagement for that
    exposure, not "no data". position is always 2 for v1 (fixed slot, per
    the design memory) but recorded rather than assumed, in case that
    changes later.
    """
    __tablename__ = "explore_exposures"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, ForeignKey("story_clusters.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, default=2, nullable=False)
    exposed_at = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class EntityStat(Base):
    """
    Feed ranking redesign, piece 1 (global importance): one row per
    canonicalized entity (see app.services.entity_graph.canonicalize_entity),
    tracking how often it's mentioned across recently-updated clusters and
    whether that rate is a spike relative to its own history. Recomputed
    incrementally each poll cycle in app.services.poller.poll_all_sources() —
    see that function for the decay/reactivation math. Feeds
    StoryCluster.entity_boost; not read anywhere else yet.
    """
    __tablename__ = "entity_stats"

    entity_key = Column(String(255), primary_key=True)
    # Best/most common original casing seen for this key, for admin/debug
    # inspection only — entity_key (not this) is what's actually matched on.
    display_name = Column(String(255), nullable=False)
    mention_count_decayed = Column(Float, default=0.0, nullable=False)
    baseline_rate = Column(Float, default=0.0, nullable=False)
    last_mentioned_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


# Indexes as defined in handoff doc
Index("idx_articles_published_at", Article.published_at.desc())
Index("idx_articles_source_published", Article.source_id, Article.published_at.desc())
Index("idx_clusters_last_updated", StoryCluster.last_updated_at.desc())
Index("idx_clusters_headline_score", StoryCluster.headline_score.desc(), StoryCluster.id.desc())
