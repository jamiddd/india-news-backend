"""Followed-source push notifications: notify a follower whenever the
source they're subscribed to publishes a new article. Driven from
scripts/send_notifications.py, alongside story_updates/topic_updates.

Simpler than those two: a "new article from a followed source" is
unambiguous, unlike a cluster/topic "genuine development" which needs a
bullet-overlap heuristic to filter out paraphrased enrichment rewrites (see
story_updates' module docstring). So this reads new Article rows directly
rather than maintaining its own per-source development/state table —
Article.id is assigned in scrape order, so `Article.id > last_article_id`
is the whole detection step.
"""
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import selectinload

from app.models import Article, NotificationLog, Source, SourceFollow, User

logger = logging.getLogger(__name__)

# Same spacing/cap as followed-story/topic updates (see
# app.services.story_updates) — deliberately identical so the three
# follow-driven notification types behave predictably together.
MIN_GAP_BETWEEN_PUSHES = timedelta(hours=2)
DAILY_CAP = 6

SOURCE_UPDATES_CHANNEL = "source_updates"
MODE = "source_update"

# (device_token, title, body, cluster_id, channel_id, extra) -> delivered?
SendFn = Callable[[str, str, str, int, str, dict], Awaitable[bool]]


def _pref_enabled(user: User) -> bool:
    return (user.preferences or {}).get("source_update_notifications_enabled", True) is not False


async def latest_article_id(session, source_id: int) -> Optional[int]:
    """The newest Article.id currently published for source_id that has
    already been assigned to a cluster (see send_source_updates — an
    unclustered article can't be deep-linked, so it can't be baselined or
    pushed either), or None if there is none. Used to baseline a brand-new
    subscription so it never pushes for an article that already existed
    before the subscribe."""
    return (
        await session.execute(
            select(func.max(Article.id)).where(
                Article.source_id == source_id, Article.cluster_id.isnot(None),
            )
        )
    ).scalar()


def may_push(now: datetime, last_notified_at: Optional[datetime], sent_today: int) -> bool:
    if sent_today >= DAILY_CAP:
        return False
    return last_notified_at is None or now - last_notified_at >= MIN_GAP_BETWEEN_PUSHES


async def send_source_updates(session, now: datetime, send: SendFn) -> int:
    """Pushes each subscriber the newest article they haven't had for their
    source, within the spacing and daily limits — same "several may have
    piled up: newest only" collapse story_updates/topic_updates use, so a
    prolific source can't flood a subscriber with one push per article."""
    follows = (await session.execute(select(SourceFollow))).scalars().all()
    if not follows:
        return 0

    source_ids = {f.source_id for f in follows}
    # Only a clustered article can be deep-linked (NotificationLog.cluster_id
    # is NOT NULL, and the client opens a push via cluster_id) — an article
    # is briefly unclustered right after ingestion, so it's skipped here and
    # picked up once clustering assigns it one on a later run.
    articles = (
        await session.execute(
            select(Article)
            .where(Article.source_id.in_(source_ids), Article.cluster_id.isnot(None))
            .order_by(Article.id)
        )
    ).scalars().all()
    latest_by_source: dict[int, Article] = {}
    for a in articles:
        current = latest_by_source.get(a.source_id)
        if current is None or a.id > current.id:
            latest_by_source[a.source_id] = a

    sources = {
        s.id: s
        for s in (
            await session.execute(select(Source).where(Source.id.in_(source_ids)))
        ).scalars().all()
    }
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
        article = latest_by_source.get(follow.source_id)
        if article is None or article.id <= (follow.last_article_id or 0):
            continue
        user = users.get(follow.user_id)
        source = sources.get(follow.source_id)
        if user is None or source is None:
            continue

        if not _pref_enabled(user):
            follow.last_article_id = article.id
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

        # Claim first, then push (see send_notifications._claim's docstring):
        # the guarded UPDATE means two overlapping runs cannot both send
        # this article to the same follower.
        claimed = await session.execute(
            update(SourceFollow)
            .where(
                SourceFollow.id == follow.id,
                (SourceFollow.last_article_id.is_(None)) | (SourceFollow.last_article_id < article.id),
            )
            .values(last_article_id=article.id, last_notified_at=now)
        )
        if claimed.rowcount == 0:
            await session.rollback()
            continue
        session.add(
            NotificationLog(
                user_id=follow.user_id, cluster_id=article.cluster_id, mode=MODE,
                sent_at=now, sent_date=now.date(),
            )
        )
        await session.commit()
        sent_today[follow.user_id] += 1

        for device in list(user.device_tokens):
            ok = await send(
                device.fcm_token,
                f"New from {source.name}",
                article.title,
                article.cluster_id,
                SOURCE_UPDATES_CHANNEL,
                {"type": "source_update", "source_id": str(source.id)},
            )
            if not ok:
                await session.delete(device)
        await session.commit()
        sent += 1
    return sent
