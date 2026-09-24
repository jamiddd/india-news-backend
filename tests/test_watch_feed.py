"""
The Watch tab's video filter (app/services/watch_feed.py).

The rule decides which stories the app's pinned horizontal player is offered,
so what matters is that Shorts / unknown-shape YouTube and PIB releases stay
out, that direct streams stay in, and that the pagination cursor round-trips.
No DB/network — the clause is compiled, not executed.
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import StoryCluster
from app.services.watch_feed import decode_cursor, encode_cursor, has_shorts_video, has_watchable_video


def _sql() -> str:
    query = select(StoryCluster.id).where(has_watchable_video())
    return str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


class TestWatchableVideoClause:
    def test_correlated_exists_not_a_join(self):
        sql = _sql()
        # A join onto articles from the outer query would fan out one
        # cluster row per video; the articles join lives inside the EXISTS.
        assert "WHERE EXISTS" in sql
        assert sql.index("WHERE EXISTS") < sql.index("JOIN sources")
        assert "articles.cluster_id = story_clusters.id" in sql

    def test_requires_a_video_url(self):
        sql = _sql()
        assert "articles.video_url IS NOT NULL" in sql
        assert "articles.video_url != ''" in sql

    def test_youtube_only_when_known_horizontal(self):
        sql = _sql()
        assert "youtube.com" in sql and "youtu.be" in sql
        assert "articles.video_is_short IS false" in sql
        # ...and a direct stream is admitted by NOT being YouTube at all.
        assert "NOT (articles.video_url ILIKE" in sql

    def test_admits_pending_brightcove(self):
        sql = _sql()
        assert "articles.brightcove_account_id IS NOT NULL" in sql
        assert "articles.brightcove_player_id IS NOT NULL" in sql
        assert "articles.brightcove_video_id IS NOT NULL" in sql

    def test_excludes_pib(self):
        sql = _sql().lower()
        assert "%pib%" in sql
        assert "%press information bureau%" in sql
        assert "%pib.gov.in%" in sql


class TestShortsClause:
    def _sql(self) -> str:
        query = select(StoryCluster.id).where(has_shorts_video())
        return str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    def test_youtube_only(self):
        sql = self._sql()
        assert "articles.video_url ILIKE '%%youtube.com%%'" in sql
        # Not negated (that is the watch clause's direct-stream branch).
        assert "NOT (articles.video_url ILIKE" not in sql

    def test_unknown_shape_counts_as_short(self):
        # IS NOT false admits both true and NULL — unknown is portrait-safe.
        assert "articles.video_is_short IS NOT false" in self._sql()

    def test_excludes_pib(self):
        assert "%%pib%%" in self._sql().lower()


class TestCursor:
    def test_round_trip(self):
        ts = datetime(2026, 9, 23, 10, 30, 15, 123456, tzinfo=timezone.utc)
        assert decode_cursor(encode_cursor(ts, 42)) == (ts, 42)

    def test_missing_cursor_starts_over(self):
        assert decode_cursor(None) is None
        assert decode_cursor("") is None

    def test_malformed_cursor_starts_over(self):
        assert decode_cursor("garbage") is None
        assert decode_cursor("2026-09-23T10:00:00+00:00|notint") is None
        assert decode_cursor("notadate|5") is None
