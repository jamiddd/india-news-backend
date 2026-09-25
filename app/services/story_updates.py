"""Followed-story push notifications: detect a genuine new development on a
followed cluster, fan it out to its followers, and end follows that have gone
quiet. Driven from scripts/send_notifications.py (same job lease, same FCM
sender), in three steps: detect_developments -> send_story_updates ->
expire_quiet_follows.

What counts as "genuine". Enrichment (app/services/enrichment.py) rewrites
StoryCluster.summary about hourly after new outlets join, and most rewrites
are paraphrases. A development therefore needs ALL of:
  1. the cluster was re-enriched since we last looked,
  2. a new outlet joined since the last look (real new reporting),
  3. some current summary bullet shares fewer than NEW_BULLET_MAX_OVERLAP of
     its content words with EVERY bullet we already know about.
Every bullet seen is then remembered, so a paraphrase of a point that already
fired (or was in the baseline) can never fire again.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Iterable, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload

from app.models import (
    ClusterFollowState,
    NotificationLog,
    StoryCluster,
    StoryDevelopment,
    StoryFollow,
    User,
)
from app.services.daily_brief_select import is_runaway_cluster

logger = logging.getLogger(__name__)

# Jaccard overlap of content words at or above which a bullet counts as a
# restatement of a known one. Tune against real data (see the dry-run in the
# feature plan) — too high floods followers with paraphrases, too low hides
# real updates.
NEW_BULLET_MAX_OVERLAP = 0.5

# At most one push per followed story per this long, and at most this many
# story-update pushes per user per UTC day across all followed stories.
MIN_GAP_BETWEEN_PUSHES = timedelta(hours=2)
DAILY_CAP = 6

# A follow ends once its story has had no new article for this long.
QUIET_EXPIRY = timedelta(hours=72)

STORY_UPDATES_CHANNEL = "story_updates"
MODE = "story_update"

# Only developments this recent are considered for sending; anything older
# than the follow expiry can't matter to a live follow.
_DEVELOPMENT_LOOKBACK = QUIET_EXPIRY + timedelta(hours=24)

# (device_token, title, body, cluster_id, channel_id, extra) -> delivered?
# False means FCM reported the token dead. Injected by send_notifications.py so
# this module needs no firebase import and stays testable.
SendFn = Callable[[str, str, str, int, str, dict], Awaitable[bool]]

_STOPWORDS = frozenset(
    "a an the and or but of in on at to for from by with as is are was were be been "
    "being it its this that these those has have had will would can could may might "
    "said says say after before over into than then also not no more most new been "
    "their his her they he she we you who whom which what when where while about "
    "against between during up down out off under again further once".split()
)
_WORD = re.compile(r"[a-z0-9]+")


def content_words(text: str) -> frozenset[str]:
    return frozenset(
        w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS and len(w) > 1
    )


def overlap(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two word sets; 0.0 if either is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def split_bullets(summary: Optional[str]) -> list[str]:
    """StoryCluster.summary is stored as "\\n• a\\n• b" (see
    enrichment.apply_ai_response)."""
    if not summary:
        return []
    out = []
    for line in summary.splitlines():
        line = line.strip().lstrip("•-*").strip()
        if line:
            out.append(line)
    return out


def find_new_bullet(
    current: Iterable[str],
    known: Iterable[str],
    max_overlap: float = NEW_BULLET_MAX_OVERLAP,
) -> Optional[str]:
    """The current bullet least like anything already known, provided it is
    below max_overlap against every known bullet; None if nothing is new."""
    known_sets = [content_words(k) for k in known]
    best: Optional[str] = None
    best_score = max_overlap
    for bullet in current:
        words = content_words(bullet)
        if not words:
            continue
        score = max((overlap(words, k) for k in known_sets), default=0.0)
        if score < best_score:
            best, best_score = bullet, score
    return best


def is_genuine_development(
    *,
    enriched_since_check: bool,
    source_count: int,
    last_source_count: int,
    new_bullet: Optional[str],
) -> bool:
    return bool(new_bullet) and enriched_since_check and source_count > last_source_count


def may_push(
    now: datetime,
    last_notified_at: Optional[datetime],
    sent_today: int,
) -> bool:
    if sent_today >= DAILY_CAP:
        return False
    return last_notified_at is None or now - last_notified_at >= MIN_GAP_BETWEEN_PUSHES


def follow_expired(now: datetime, cluster_last_updated: datetime, followed_at: datetime) -> bool:
    """Quiet for QUIET_EXPIRY, measured from whichever is later — the last
    article or the moment the user followed — so following an already-old
    story doesn't end the follow on the very next run."""
    return now - max(cluster_last_updated, followed_at) >= QUIET_EXPIRY


def _pref_enabled(user: User) -> bool:
    return (user.preferences or {}).get("story_update_notifications_enabled", True) is not False


async def seed_follow_state(session, cluster: StoryCluster, now: datetime) -> None:
    """Baseline for a cluster's first follow: everything in today's summary is
    already known, so following never pushes what the user is looking at.
    No-op if the cluster is already tracked."""
    existing = await session.get(ClusterFollowState, cluster.id)
    if existing is not None:
        return
    session.add(
        ClusterFollowState(
            cluster_id=cluster.id,
            known_bullets=split_bullets(cluster.summary),
            last_source_count=cluster.distinct_source_count or 0,
            last_checked_at=now,
        )
    )


async def detect_developments(session, now: datetime) -> int:
    """Records a StoryDevelopment for each followed cluster that genuinely
    developed since it was last looked at. Returns how many."""
    rows = (
        await session.execute(
            select(StoryCluster, ClusterFollowState)
            .join(ClusterFollowState, ClusterFollowState.cluster_id == StoryCluster.id)
            .where(StoryCluster.id.in_(select(StoryFollow.cluster_id)))
        )
    ).all()

    found = 0
    for cluster, state in rows:
        if cluster.last_enriched_at is None or cluster.last_enriched_at <= state.last_checked_at:
            continue  # no new enrichment since the last look

        current = split_bullets(cluster.summary)
        known = list(state.known_bullets or [])
        new_bullet = find_new_bullet(current, known)
        source_count = cluster.distinct_source_count or 0
        runaway = is_runaway_cluster(cluster.article_count, cluster.distinct_source_count)

        if not runaway and is_genuine_development(
            enriched_since_check=True,
            source_count=source_count,
            last_source_count=state.last_source_count,
            new_bullet=new_bullet,
        ):
            session.add(
                StoryDevelopment(
                    cluster_id=cluster.id,
                    headline=cluster.headline,
                    bullet=new_bullet,
                    source_count=source_count,
                    detected_at=now,
                )
            )
            found += 1

        # Remember everything seen either way, so restatements never fire.
        state.known_bullets = known + [b for b in current if b not in known]
        state.last_source_count = source_count
        state.last_checked_at = now

    await session.commit()
    return found


async def send_story_updates(session, now: datetime, send: SendFn) -> int:
    """Pushes each follower the newest development they haven't had, within
    the spacing and daily limits. Returns pushes attempted."""
    follows = (
        await session.execute(
            select(StoryFollow).options(selectinload(StoryFollow.cluster))
        )
    ).scalars().all()
    if not follows:
        return 0

    devs_by_cluster: dict[int, list[StoryDevelopment]] = {}
    dev_rows = (
        await session.execute(
            select(StoryDevelopment)
            .where(
                StoryDevelopment.cluster_id.in_({f.cluster_id for f in follows}),
                StoryDevelopment.detected_at >= now - _DEVELOPMENT_LOOKBACK,
            )
            .order_by(StoryDevelopment.id)
        )
    ).scalars().all()
    for d in dev_rows:
        devs_by_cluster.setdefault(d.cluster_id, []).append(d)

    users = {
        u.id: u
        for u in (
            await session.execute(
                select(User)
                .options(selectinload(User.device_tokens))
                .where(User.id.in_({f.user_id for f in follows}))
            )
        ).scalars().unique().all()
    }

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent_today: dict[str, int] = {}
    sent = 0

    for follow in follows:
        candidates = [
            d
            for d in devs_by_cluster.get(follow.cluster_id, [])
            if d.detected_at >= follow.created_at and d.id > (follow.last_development_id or 0)
        ]
        if not candidates:
            continue
        dev = candidates[-1]  # several may have piled up behind the spacing limit: newest only
        user = users.get(follow.user_id)
        if user is None:
            continue

        if not _pref_enabled(user):
            # Switch off: skip past it so re-enabling doesn't replay old news.
            follow.last_development_id = dev.id
            await session.commit()
            continue

        if follow.user_id not in sent_today:
            sent_today[follow.user_id] = (
                await session.execute(
                    select(func.count(NotificationLog.id)).where(
                        NotificationLog.user_id == follow.user_id,
                        NotificationLog.mode == MODE,
                        NotificationLog.sent_at >= today_start,
                    )
                )
            ).scalar() or 0
        if not may_push(now, follow.last_notified_at, sent_today[follow.user_id]):
            continue

        # Claim first, then push (see send_notifications._claim): the guarded
        # UPDATE means two overlapping runs cannot both send this development.
        claimed = await session.execute(
            update(StoryFollow)
            .where(
                StoryFollow.id == follow.id,
                (StoryFollow.last_development_id.is_(None)) | (StoryFollow.last_development_id < dev.id),
            )
            .values(last_development_id=dev.id, last_notified_at=now)
        )
        if claimed.rowcount == 0:
            await session.rollback()
            continue
        session.add(
            NotificationLog(
                user_id=follow.user_id, cluster_id=follow.cluster_id, mode=MODE,
                sent_at=now, sent_date=now.date(),
            )
        )
        await session.commit()
        sent_today[follow.user_id] += 1

        for device in list(user.device_tokens):
            ok = await send(
                device.fcm_token,
                f"Update: {dev.headline}",
                dev.bullet,
                follow.cluster_id,
                STORY_UPDATES_CHANNEL,
                {"type": "story_update"},
            )
            if not ok:
                await session.delete(device)
        await session.commit()
        sent += 1
    return sent


async def expire_quiet_follows(session, now: datetime, send: SendFn) -> int:
    """Ends follows whose story has gone quiet, telling the user why (unless
    they've switched story alerts off). Returns follows ended."""
    follows = (
        await session.execute(
            select(StoryFollow).options(selectinload(StoryFollow.cluster))
        )
    ).scalars().all()
    # Drop detection state for clusters nobody follows any more (covers
    # unfollows through the API as well as expiries).
    await session.execute(
        delete(ClusterFollowState).where(
            ClusterFollowState.cluster_id.notin_(select(StoryFollow.cluster_id))
        )
    )
    await session.commit()

    expired = [f for f in follows if follow_expired(now, f.cluster.last_updated_at, f.created_at)]
    if not expired:
        return 0

    users = {
        u.id: u
        for u in (
            await session.execute(
                select(User)
                .options(selectinload(User.device_tokens))
                .where(User.id.in_({f.user_id for f in expired}))
            )
        ).scalars().unique().all()
    }

    for follow in expired:
        user = users.get(follow.user_id)
        headline = follow.cluster.headline
        cluster_id = follow.cluster_id
        await session.delete(follow)
        await session.commit()  # end the follow first so a failed push can't re-fire it
        if user is None or not _pref_enabled(user):
            continue
        for device in list(user.device_tokens):
            ok = await send(
                device.fcm_token,
                "No longer following",
                f"No new developments on \"{headline}\" in 3 days, so we've stopped following it. Tap to catch up.",
                cluster_id,
                STORY_UPDATES_CHANNEL,
                {"type": "follow_ended"},
            )
            if not ok:
                await session.delete(device)
        await session.commit()

    return len(expired)
