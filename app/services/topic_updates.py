"""Followed-topic push notifications: detect a genuine new development
anywhere under a followed topic — a brand-new matching story, or a
genuinely new bullet on one already matched — and fan it out to its
followers. Driven from scripts/send_notifications.py, in two steps:
detect_topic_developments -> send_topic_updates, plus a housekeeping
expire_orphaned_topic_state.

Same "topic" as /search and TagFeedScreen's "Stories related to X": an
ILIKE match against StoryCluster.headline/summary (see
app.services.cluster_search.search_clusters). A topic can therefore match
many clusters, unlike a followed story which is exactly one cluster —
detect_topic_developments can record several TopicDevelopment rows for the
same topic in one run (one cluster breaking, another gaining a bullet),
and send_topic_updates deliberately sends only the newest one per follow
per run, the same "several may have piled up: newest only" collapse
app.services.story_updates uses. Combined with MIN_GAP_BETWEEN_PUSHES this
is what keeps a broad topic (hundreds of matching stories) down to at most
one push per spacing window instead of one per matching story.

What counts as "genuine" reuses story_updates' bullet-overlap heuristic
verbatim (same NEW_BULLET_MAX_OVERLAP, same content_words/overlap/
find_new_bullet) — see that module's docstring for the reasoning.
"""
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import selectinload

from app.models import (
    StoryCluster,
    NotificationLog,
    TopicClusterState,
    TopicDevelopment,
    TopicFollow,
    User,
)
from app.services.daily_brief_select import is_runaway_cluster
from app.services.story_updates import (
    NEW_BULLET_MAX_OVERLAP,
    find_new_bullet,
    split_bullets,
)

logger = logging.getLogger(__name__)

# Same spacing/cap as followed-story updates (see app.services.story_updates)
# — deliberately identical so the two features behave predictably together.
MIN_GAP_BETWEEN_PUSHES = timedelta(hours=2)
DAILY_CAP = 6

# How far back a matching cluster can have last updated and still be
# considered live for a topic. Bounds the cost of a broad topic (e.g. a
# country name) matching thousands of clusters over the app's history —
# nothing older than this can plausibly be "developing" right now.
MATCH_LOOKBACK = timedelta(days=30)

# Cap on matching clusters examined per topic per run, newest-updated first.
# A topic can match far more than this; only the most recently active
# clusters can plausibly be mid-development, and the rest would just be
# baseline-seeded busywork on every run.
MAX_CANDIDATES_PER_TOPIC = 30

# Only developments this recent are considered for sending; anything older
# can't matter to a follow created after it (see send_topic_updates).
_DEVELOPMENT_LOOKBACK = MATCH_LOOKBACK + timedelta(days=1)

TOPIC_UPDATES_CHANNEL = "topic_updates"
MODE = "topic_update"

# (device_token, title, body, cluster_id, channel_id, extra) -> delivered?
SendFn = Callable[[str, str, str, int, str, dict], Awaitable[bool]]


def normalize_topic_key(topic: str) -> str:
    return " ".join((topic or "").strip().lower().split())


def _pref_enabled(user: User) -> bool:
    return (user.preferences or {}).get("topic_update_notifications_enabled", True) is not False


async def _matching_clusters(session, topic_key: str, now: datetime) -> list[StoryCluster]:
    pattern = f"%{topic_key}%"
    result = await session.execute(
        select(StoryCluster)
        .where(
            or_(
                func.lower(StoryCluster.headline).like(pattern),
                func.lower(StoryCluster.summary).like(pattern),
            ),
            StoryCluster.last_updated_at >= now - MATCH_LOOKBACK,
        )
        .order_by(StoryCluster.last_updated_at.desc())
        .limit(MAX_CANDIDATES_PER_TOPIC)
    )
    return list(result.scalars().all())


async def seed_topic_follow_state(session, topic_key: str, now: datetime) -> None:
    """Baseline for a topic's first follow: every cluster already matching
    it is already known, so following never pushes what the user is
    already seeing in the tag feed. No-op for clusters already tracked
    (i.e. this is the second+ follower of the same topic_key)."""
    clusters = await _matching_clusters(session, topic_key, now)
    if not clusters:
        return
    existing_ids = set(
        (
            await session.execute(
                select(TopicClusterState.cluster_id).where(
                    TopicClusterState.topic_key == topic_key,
                    TopicClusterState.cluster_id.in_([c.id for c in clusters]),
                )
            )
        ).scalars().all()
    )
    for cluster in clusters:
        if cluster.id in existing_ids:
            continue
        session.add(
            TopicClusterState(
                topic_key=topic_key,
                cluster_id=cluster.id,
                known_bullets=split_bullets(cluster.summary),
                last_source_count=cluster.distinct_source_count or 0,
                first_seen_at=now,
                last_checked_at=now,
            )
        )


async def detect_topic_developments(session, now: datetime) -> int:
    """Records a TopicDevelopment for each followed topic that genuinely
    developed since it was last looked at. Returns how many."""
    topic_keys = (
        await session.execute(select(TopicFollow.topic_key).distinct())
    ).scalars().all()
    if not topic_keys:
        return 0

    found = 0
    for topic_key in topic_keys:
        clusters = await _matching_clusters(session, topic_key, now)
        if not clusters:
            continue

        states = {
            s.cluster_id: s
            for s in (
                await session.execute(
                    select(TopicClusterState).where(
                        TopicClusterState.topic_key == topic_key,
                        TopicClusterState.cluster_id.in_([c.id for c in clusters]),
                    )
                )
            ).scalars().all()
        }

        for cluster in clusters:
            state = states.get(cluster.id)
            source_count = cluster.distinct_source_count or 0
            runaway = is_runaway_cluster(cluster.article_count, cluster.distinct_source_count)

            if state is None:
                # Started matching this topic after it was seeded/last seen
                # in full — a brand-new story on the topic.
                current = split_bullets(cluster.summary)
                if not runaway:
                    session.add(
                        TopicDevelopment(
                            topic_key=topic_key,
                            cluster_id=cluster.id,
                            headline=cluster.headline,
                            bullet=current[0] if current else cluster.headline,
                            source_count=source_count,
                            kind="new_story",
                            detected_at=now,
                        )
                    )
                    found += 1
                session.add(
                    TopicClusterState(
                        topic_key=topic_key,
                        cluster_id=cluster.id,
                        known_bullets=current,
                        last_source_count=source_count,
                        first_seen_at=now,
                        last_checked_at=now,
                    )
                )
                continue

            if cluster.last_enriched_at is None or cluster.last_enriched_at <= state.last_checked_at:
                continue  # no new enrichment since the last look

            current = split_bullets(cluster.summary)
            known = list(state.known_bullets or [])
            new_bullet = find_new_bullet(current, known, max_overlap=NEW_BULLET_MAX_OVERLAP)

            if not runaway and new_bullet and source_count > state.last_source_count:
                session.add(
                    TopicDevelopment(
                        topic_key=topic_key,
                        cluster_id=cluster.id,
                        headline=cluster.headline,
                        bullet=new_bullet,
                        source_count=source_count,
                        kind="new_bullet",
                        detected_at=now,
                    )
                )
                found += 1

            state.known_bullets = known + [b for b in current if b not in known]
            state.last_source_count = source_count
            state.last_checked_at = now

    await session.commit()
    return found


def may_push(now: datetime, last_notified_at: Optional[datetime], sent_today: int) -> bool:
    if sent_today >= DAILY_CAP:
        return False
    return last_notified_at is None or now - last_notified_at >= MIN_GAP_BETWEEN_PUSHES


def _push_copy(follow: TopicFollow, dev: TopicDevelopment) -> tuple[str, str]:
    if dev.kind == "new_story":
        return f'New on "{follow.topic}"', dev.headline
    return f'Update on "{follow.topic}"', dev.bullet


async def send_topic_updates(session, now: datetime, send: SendFn) -> int:
    """Pushes each follower the newest development they haven't had for
    their topic, within the spacing and daily limits. Several developments
    can pile up behind those limits for a busy topic; only the newest is
    ever sent, so a follower gets at most one push per run regardless of
    how many matching clusters moved. Returns pushes attempted."""
    follows = (await session.execute(select(TopicFollow))).scalars().all()
    if not follows:
        return 0

    topic_keys = {f.topic_key for f in follows}
    devs_by_topic: dict[str, list[TopicDevelopment]] = {}
    dev_rows = (
        await session.execute(
            select(TopicDevelopment)
            .where(
                TopicDevelopment.topic_key.in_(topic_keys),
                TopicDevelopment.detected_at >= now - _DEVELOPMENT_LOOKBACK,
            )
            .order_by(TopicDevelopment.id)
        )
    ).scalars().all()
    for d in dev_rows:
        devs_by_topic.setdefault(d.topic_key, []).append(d)

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
            for d in devs_by_topic.get(follow.topic_key, [])
            if d.detected_at >= follow.created_at and d.id > (follow.last_development_id or 0)
        ]
        if not candidates:
            continue
        dev = candidates[-1]
        user = users.get(follow.user_id)
        if user is None:
            continue

        if not _pref_enabled(user):
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
            update(TopicFollow)
            .where(
                TopicFollow.id == follow.id,
                (TopicFollow.last_development_id.is_(None)) | (TopicFollow.last_development_id < dev.id),
            )
            .values(last_development_id=dev.id, last_notified_at=now)
        )
        if claimed.rowcount == 0:
            await session.rollback()
            continue
        session.add(
            NotificationLog(
                user_id=follow.user_id, cluster_id=dev.cluster_id, mode=MODE,
                sent_at=now, sent_date=now.date(),
            )
        )
        await session.commit()
        sent_today[follow.user_id] += 1

        title, body = _push_copy(follow, dev)
        for device in list(user.device_tokens):
            ok = await send(
                device.fcm_token, title, body, dev.cluster_id,
                TOPIC_UPDATES_CHANNEL, {"type": "topic_update", "topic": follow.topic},
            )
            if not ok:
                await session.delete(device)
        await session.commit()
        sent += 1
    return sent


async def expire_orphaned_topic_state(session) -> None:
    """Drops detection state for topics nobody follows any more (covers
    unfollows through the API), the same housekeeping
    story_updates.expire_quiet_follows does for ClusterFollowState."""
    await session.execute(
        delete(TopicClusterState).where(
            TopicClusterState.topic_key.notin_(select(TopicFollow.topic_key).distinct())
        )
    )
    await session.commit()
