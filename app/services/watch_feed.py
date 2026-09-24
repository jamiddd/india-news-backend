"""Which stories belong on the app's Watch tab (and its Swipe / Shorts view).

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
included: the list doesn't resolve it, the app does so for the one video it
is about to play.

PIB (Press Information Bureau) releases are excluded to match the app's own
StoryCluster.galleryMedia, which never surfaces a PIB article's video —
listing a story whose only video the client would then refuse to show would
put an unplayable row on the tab.

The Swipe view is the mirror image: YouTube videos that are Shorts or of
unknown shape (video_is_short IS NOT FALSE). Unknown counts as portrait there
for the same reason it is excluded above — the app's portrait-safe path is
the only one that can't look broken for a video of unknown shape. Direct
streams never qualify: nothing records their orientation.

Lives here rather than in main.py so it is testable without importing the
whole FastAPI app, and so the app's client-side filter and this one stay
described in one place.
"""
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import and_, exists, not_, or_, select

from app.models import Article, Source, StoryCluster
from app.services.video_probe import MIN_VIDEO_SECONDS as MIN_WATCH_SECONDS


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


def _has_pending_brightcove():
    """video_url is NULL only because the expiring manifest was dropped at
    scrape time; the app re-resolves it via GET /articles/{id}/video-url."""
    return and_(
        or_(Article.video_url.is_(None), Article.video_url == ""),
        Article.brightcove_account_id.isnot(None),
        Article.brightcove_player_id.isnot(None),
        Article.brightcove_video_id.isnot(None),
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
            or_(
                and_(
                    Article.video_url.isnot(None),
                    Article.video_url != "",
                    or_(not_(_is_youtube()), Article.video_is_short.is_(False)),
                    # Too short for the Watch tab; unknown length is kept.
                    or_(
                        Article.video_duration_seconds.is_(None),
                        Article.video_duration_seconds >= MIN_WATCH_SECONDS,
                    ),
                ),
                _has_pending_brightcove(),
            ),
            not_(_is_pib()),
        )
    )


def has_shorts_video():
    """EXISTS clause for the Swipe view: a YouTube Short, or a YouTube video whose
    shape we couldn't determine (see the module docstring)."""
    return exists(
        select(Article.id)
        .join(Source, Source.id == Article.source_id)
        .where(
            Article.cluster_id == StoryCluster.id,
            Article.video_url.isnot(None),
            Article.video_url != "",
            _is_youtube(),
            Article.video_is_short.isnot(False),
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
