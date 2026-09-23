"""Which stories belong on the app's Watch tab.

The Watch tab pins a *horizontal* player over a list of other videos, so a
story qualifies only when it carries a video that fits that player:

  * a direct stream (og:video, JW, Brightcove, native <video>) — anything
    that isn't YouTube, or
  * a YouTube video known to be horizontal (video_is_short IS FALSE).

A YouTube Short, or one whose shape we couldn't determine (video_is_short
NULL — see models.Article.video_is_short, "NULL is unknown, not no"), would
letterbox badly in the pinned player, so it is left to the feed and story
detail, which have a portrait-safe path. An article whose video_url is NULL
because its Brightcove manifest was an expiring link (has_pending_video) is
also skipped: it can't be played without a per-article re-resolve the list
can't afford.

PIB (Press Information Bureau) releases are excluded to match the app's own
StoryCluster.galleryMedia, which never surfaces a PIB article's video —
listing a story whose only video the client would then refuse to show would
put an unplayable row on the tab.

Lives here rather than in main.py so it is testable without importing the
whole FastAPI app, and so the app's client-side filter and this one stay
described in one place.
"""
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import and_, exists, not_, or_, select

from app.models import Article, Source, StoryCluster


def _is_youtube():
    return or_(
        Article.video_url.ilike("%youtube.com%"),
        Article.video_url.ilike("%youtu.be%"),
    )


def _is_pib():
    return or_(
        Source.name.ilike("%pib%"),
        Source.name.ilike("%press information bureau%"),
        Article.url.ilike("%pib.gov.in%"),
    )


def has_watchable_video():
    """EXISTS clause: this cluster has at least one article the Watch tab can play.

    A correlated EXISTS rather than a join onto Article, so it can't fan out
    StoryCluster rows (one per matching article) the way a plain join would.
    """
    return exists(
        select(Article.id)
        .join(Source, Source.id == Article.source_id)
        .where(
            Article.cluster_id == StoryCluster.id,
            Article.video_url.isnot(None),
            Article.video_url != "",
            or_(not_(_is_youtube()), Article.video_is_short.is_(False)),
            not_(_is_pib()),
        )
    )


def encode_cursor(last_updated_at: datetime, cluster_id: int) -> str:
    """Compound cursor: last_updated_at isn't monotonic with id, so a bare id
    can't express "everything after this row" in last_updated_at order."""
    return f"{last_updated_at.isoformat()}|{cluster_id}"


def decode_cursor(cursor: Optional[str]) -> Optional[Tuple[datetime, int]]:
    """None for a missing or malformed cursor — treated as "start over" rather
    than a 500, same as the All Stories cursor in GET /clusters."""
    if not cursor:
        return None
    try:
        ts_str, id_str = cursor.rsplit("|", 1)
        return datetime.fromisoformat(ts_str), int(id_str)
    except (ValueError, TypeError):
        return None
