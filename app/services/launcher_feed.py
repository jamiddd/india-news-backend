"""Pure mapping/selection logic for the launcher feed (GET /api/v1/launcher/feed).

No FastAPI or DB imports on purpose, so it is unit-testable without the app.
The route in app/launcher_feed.py owns the query and the cache; this module
turns a loaded StoryCluster into the flat card the Spot launcher renders.

The launcher is a thin client: one flat card per story (tag, source, title,
time, image). It never gets article bodies, publisher URLs or video fields, and
a card tap goes to the News app, not the publisher.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from app.services.image_extractor import is_hd_image

# Sources whose lead images are not suitable as a card photo (government
# press releases etc.). Mirrors the app's "first non-PIB article with an
# image" rule noted in main.py's _cluster_to_list_out.
_IMAGE_SKIP_SOURCE_NAMES = {"pib"}


def _is_pib(source_name: Optional[str]) -> bool:
    return bool(source_name) and any(
        skip in source_name.lower() for skip in _IMAGE_SKIP_SOURCE_NAMES
    )


def pick_primary_article(articles: list[Any]) -> Optional[Any]:
    """Choose the article that supplies the card's source, image and time.

    Preference: a non-PIB article with an image (HD first, then newest);
    otherwise the newest article. Deterministic, so a cached page is stable.
    """
    if not articles:
        return None

    def source_name(art: Any) -> Optional[str]:
        src = getattr(art, "source", None)
        return src.name if src is not None else None

    with_image = [
        art for art in articles
        if getattr(art, "image_url", None) and not _is_pib(source_name(art))
    ]
    if with_image:
        return max(
            with_image,
            key=lambda art: (
                is_hd_image(getattr(art, "image_width", None), getattr(art, "image_height", None)),
                art.published_at,
                art.id,
            ),
        )
    return max(articles, key=lambda art: (art.published_at, art.id))


def display_tag(category: Optional[str]) -> str:
    """Source.category slug -> card label ('northeast' -> 'Northeast')."""
    if not category or category.lower() in {"general", "all"}:
        return "News"
    return category.replace("_", " ").strip().title()


# --- Teaser ----------------------------------------------------------------
# One framing angle shown on the card: the free sample of what the News app
# does (compare how outlets cover the same story). The full comparison is never
# sent to the launcher.

# A card only earns a teaser when enough outlets cover the story...
TEASER_MIN_SOURCES = 3
# ...and the comparison has at least this many outlets with a usable angle.
TEASER_MIN_FRAMING_OUTLETS = 2
# Editorial caution, mirroring the marketing-site hero (main.py
# HERO_SAFE_CATEGORIES): only surface a framing comparison, out of the News
# app's own context, for these source categories. Politics, official and
# regional stories stay un-teased.
TEASER_SAFE_CATEGORIES = frozenset({"national", "world", "business"})
# Same staleness rule as main.py's _framing_for_response (FRAMING_MAX_AGE):
# a comparison the pipeline has stopped refreshing is not shown. NULL
# last_enriched_at (backfilled rows) is served as-is there, and here.
FRAMING_MAX_AGE = timedelta(hours=36)


def _framing_entries(framing: Any) -> list[dict]:
    """Usable {outlet, headline_angle} rows from StoryCluster.framing_comparison
    (the enrichment prompt's shape: a list of those two string fields)."""
    if not isinstance(framing, list):
        return []
    entries = []
    for raw in framing:
        if not isinstance(raw, dict):
            continue
        outlet = raw.get("outlet")
        angle = raw.get("headline_angle")
        if isinstance(outlet, str) and isinstance(angle, str) and outlet.strip() and angle.strip():
            entries.append({"outlet": outlet.strip(), "headline_angle": angle.strip()})
    return entries


def _framing_is_fresh(last_enriched_at: Optional[datetime], now: datetime) -> bool:
    if last_enriched_at is None:
        return True
    if last_enriched_at.tzinfo is None:
        last_enriched_at = last_enriched_at.replace(tzinfo=timezone.utc)
    return now - last_enriched_at <= FRAMING_MAX_AGE


def build_teaser(
    cluster: Any, card_source_name: str, card_category: Optional[str], now: datetime
) -> Optional[dict]:
    """{source_count, angle:{outlet, headline_angle}} or None.

    The angle is the first usable entry whose outlet differs from the card's own
    source (so it adds information the card does not already show); deterministic
    so the cached page is stable.
    """
    source_count = getattr(cluster, "distinct_source_count", None) or 0
    if source_count < TEASER_MIN_SOURCES:
        return None
    if (card_category or "").lower() not in TEASER_SAFE_CATEGORIES:
        return None
    if not _framing_is_fresh(getattr(cluster, "last_enriched_at", None), now):
        return None
    entries = _framing_entries(getattr(cluster, "framing_comparison", None))
    if len(entries) < TEASER_MIN_FRAMING_OUTLETS:
        return None
    others = [e for e in entries if e["outlet"].lower() != (card_source_name or "").lower()]
    chosen = (others or entries)[0]
    return {"source_count": source_count, "angle": chosen}


def cluster_to_story_item(cluster: Any, now: Optional[datetime] = None) -> Optional[dict]:
    """StoryCluster (with articles+source loaded) -> launcher `story` item.

    Returns None for a cluster with no articles (nothing to show).
    """
    now = now or datetime.now(timezone.utc)
    primary = pick_primary_article(list(cluster.articles))
    if primary is None:
        return None
    src = getattr(primary, "source", None)
    source_name = src.name if src is not None else "Unknown"
    item: dict[str, Any] = {
        "type": "story",
        "id": f"c_{cluster.id}",
        "cluster_id": cluster.id,
        "tag": display_tag(getattr(src, "category", None)),
        "source_name": source_name,
        "title": cluster.headline or primary.title,
        "published_at": primary.published_at,
        "image_url": primary.image_url,
        "teaser": build_teaser(cluster, source_name, getattr(src, "category", None), now),
    }
    return item


# --- Promo placement -------------------------------------------------------
# Promo items (the end-of-feed continuation card now; the daily game tile
# later) are inserted AFTER the cache read, so the cached page stays shared and
# promo-free. These rules are enforced here, not trusted to the client.

# No promo among the first N items.
PROMO_MIN_INDEX = 3
# At least this many stories between any two promos (1 promo per 8 stories).
PROMO_MIN_STORIES_BETWEEN = 8
# A brand-new launcher install sees no promos for this many days.
PROMO_MIN_SESSION_AGE_DAYS = 2
# The continuation card goes at the end of page 1, only if the page is this long.
CONTINUATION_MIN_STORIES = 10
CONTINUATION_PROMO_ID = "promo_continue"


def continuation_promo() -> dict:
    return {
        "type": "promo",
        "id": CONTINUATION_PROMO_ID,
        "variant": "A",
        "title": "Want the full picture?",
        "body": "See how every outlet covered these stories, side by side, in the News app.",
        "dismissible": True,
        "cta": {
            "label": "Get the News app",
            "target": "news_app",
            "deeplink": None,
            "campaign": "continue",
            "content": "feed_end",
        },
    }


# --- Daily game tile -------------------------------------------------------
# One daily puzzle card in the 4th slot of page 1. Unlike the install promos it
# is also served to launchers that already have the News app (it then opens the
# game directly), so it is exempt from the news_installed and young-session
# suppression for those users. Rotation is by date; every game's /daily route
# creates the puzzle on demand, so none can be "missing". Keep GAME_TYPES in sync
# with the News app's util/GameLinks.TYPES.
GAME_TYPES = ("crossword", "wordle", "sudoku", "word_search", "spelling_bee", "word_ladder", "quiz")
GAME_TILE_INDEX = 3
GAME_LINK_BASE = "https://openindiannews.com/games"
_GAME_TITLES = {
    "crossword": "Today's Crossword",
    "wordle": "Today's Wordle",
    "sudoku": "Today's Sudoku",
    "word_search": "Today's Word Search",
    "spelling_bee": "Today's Spelling Bee",
    "word_ladder": "Today's Word Ladder",
    "quiz": "Today's News Quiz",
}


def game_for_date(day: date) -> str:
    return GAME_TYPES[day.toordinal() % len(GAME_TYPES)]


def game_tile(day: date) -> dict:
    game = game_for_date(day)
    return {
        "type": "game_tile",
        "id": f"game_{game}_{day.isoformat()}",
        "game": game,
        "title": _GAME_TITLES[game],
        "subtitle": "Play today's puzzle and start your streak",
        "dismissible": True,
        "cta": {
            "label": "Play",
            "target": "news_app",
            "deeplink": f"{GAME_LINK_BASE}/{game}/daily",
            "campaign": "game",
            "content": f"game_{game}",
        },
    }


def promos_allowed(news_installed: bool, session_age_days: Optional[int]) -> bool:
    """False when News is installed, or the launcher install is too new or its
    age is unknown (a caller that does not say how old it is gets no promos)."""
    if news_installed:
        return False
    if session_age_days is None or session_age_days < PROMO_MIN_SESSION_AGE_DAYS:
        return False
    return True


def insert_promos(
    stories: list[dict], candidates: Iterable[tuple[int, dict]], dismissed: Iterable[str] = ()
) -> list[dict]:
    """Merge promo candidates into a list of story items, enforcing spacing.

    `candidates` is (preferred_index_in_stories, promo) in priority order. A
    dismissed promo is dropped; a promo is clamped to PROMO_MIN_INDEX and dropped
    if it would sit within PROMO_MIN_STORIES_BETWEEN stories of one already
    placed. Pure: never mutates `stories`.
    """
    dismissed_ids = set(dismissed)
    placed: list[tuple[int, dict]] = []  # (story index it precedes, promo)
    for preferred, promo in candidates:
        if promo["id"] in dismissed_ids:
            continue
        index = min(max(preferred, PROMO_MIN_INDEX), len(stories))
        if len(stories) < PROMO_MIN_INDEX:
            continue
        if any(abs(index - other) < PROMO_MIN_STORIES_BETWEEN for other, _ in placed):
            continue
        placed.append((index, promo))
    out: list[dict] = []
    by_index: dict[int, list[dict]] = {}
    for index, promo in placed:
        by_index.setdefault(index, []).append(promo)
    for i, story in enumerate(stories):
        out.extend(by_index.get(i, []))
        out.append(story)
    out.extend(by_index.get(len(stories), []))
    return out


def apply_promos(
    stories: list[dict],
    *,
    is_first_page: bool,
    news_installed: bool,
    session_age_days: Optional[int],
    dismissed: Iterable[str] = (),
    today: Optional[date] = None,
) -> list[dict]:
    """Stories plus whatever promo-type items the rules allow for this request.

    A caller that does not say how old its install is (`session_age_days` None,
    i.e. not the launcher) gets none of them.
    """
    if session_age_days is None:
        return list(stories)
    candidates: list[tuple[int, dict]] = []
    if is_first_page and today is not None and (news_installed or session_age_days >= PROMO_MIN_SESSION_AGE_DAYS):
        candidates.append((GAME_TILE_INDEX, game_tile(today)))
    if (
        is_first_page
        and promos_allowed(news_installed, session_age_days)
        and len(stories) >= CONTINUATION_MIN_STORIES
    ):
        candidates.append((len(stories), continuation_promo()))
    return insert_promos(stories, candidates, dismissed)


def encode_cursor(score: float, cluster_id: int) -> str:
    return f"{score!r}:{cluster_id}"


def decode_cursor(cursor: Optional[str]) -> Optional[tuple[float, int]]:
    """'score:id' -> (score, id); None for absent or malformed (start over)."""
    if not cursor:
        return None
    try:
        score_str, id_str = cursor.split(":", 1)
        return float(score_str), int(id_str)
    except (ValueError, TypeError):
        return None


def isoformat_z(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")
