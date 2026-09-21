"""Admin push notifications: one for the reviewer's whole day (poll + quiz),
one for the Breaking slot's human-review queue.

The poll and the quiz are two drafts, but one person, one sitting, one moment
right after midnight. Two notifications would mean the second is either
ignored as a duplicate or actioned separately later — so this sends a single
message summarising both and deep-links to /admin, which lists them.

Timing works out without coordinating the two schedulers: the quiz/games
draft for day D is written by run_crossword_scheduler at 00:00 IST on D, and
the poll draft at 00:10 IST — 10 minutes later, deliberately, so the quiz is
always done first. By the time this fires (right after the poll draft),
both already exist. (Was 23:55 IST the night before / 04:30 IST until
2026-09-16, a ~4.5 hour gap doing the same job — merged onto the same
near-midnight window at the user's request.)

The Breaking-slot push (notify_admin_breaking_review) is a different shape —
event-driven off the poller cycle rather than once a day — but reuses the
same delivery mechanism (_push_to_admin) and the same
ADMIN_USER_EMAIL/admin_alerts channel, since it's the same person reviewing
on the same device. See backend/docs/breaking-human-review-plan.md.

notify_admin_timelines_generated is the third: sent once after the daily
timeline generation cycle (scripts/run_timeline_scheduler.py, 06:00 IST) with
a summary of what was written, and only when the cycle actually generated
something.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import DeviceToken, User
from app.services.admin_email import send_admin_email

logger = logging.getLogger(__name__)

# Per-process, per-(source, exception type) cooldown for notify_admin_failure —
# resets on restart, which is fine for a dev alert and avoids a migration.
# Keeps a pipeline that fails every poll cycle from sending dozens of emails
# a day and training the inbox to ignore them.
_FAILURE_COOLDOWN = timedelta(minutes=60)
_last_failure_alert: dict[str, datetime] = {}


async def _push_to_admin(session: AsyncSession, title: str, body: str, url: str) -> bool:
    """Shared delivery: send to the admin's registered devices AND the
    admin-alerts FCM topic, tolerating individual dead-token/permission
    failures the same way scripts/send_notifications.py does. The topic
    send is what reaches a debug install with nobody signed in — device
    tokens only exist once someone has logged in on that install. Returns
    whether at least one delivery (device or topic) succeeded."""
    try:
        from firebase_admin import messaging
        from app.services.firebase_auth import _get_firebase_app
        app = _get_firebase_app()
    except Exception:
        return False

    data = {"title": title, "body": body, "url": url, "channel_id": "admin_alerts"}
    sent = False

    admin = await session.scalar(select(User).where(User.email == settings.ADMIN_USER_EMAIL)) if settings.ADMIN_USER_EMAIL else None
    if admin:
        tokens = (await session.execute(select(DeviceToken).where(DeviceToken.user_id == admin.id))).scalars().all()
        for device in tokens:
            try:
                messaging.send(messaging.Message(
                    data=data,
                    android=messaging.AndroidConfig(priority="high"),
                    token=device.fcm_token,
                ), app=app)
                sent = True
            except Exception:
                continue

    if settings.ADMIN_ALERT_TOPIC:
        try:
            messaging.send(messaging.Message(
                data=data,
                android=messaging.AndroidConfig(priority="high"),
                topic=settings.ADMIN_ALERT_TOPIC,
            ), app=app)
            sent = True
        except Exception:
            pass

    return sent


def compose(tasks: dict[str, dict]) -> tuple[str, str] | None:
    """(title, body) for the day's review push, or None if nothing is waiting.

    Returning None matters: a notification that arrives every morning whether
    or not there is anything to do is one the reviewer learns to swipe away.
    """
    waiting = [name for name, task in tasks.items() if task["waiting"]]
    missing = [name for name, task in tasks.items() if not task["exists"]]

    if not waiting and not missing:
        return None

    if waiting and missing:
        title = f"{len(waiting)} draft to review, {len(missing)} missing"
    elif waiting:
        title = ("Poll and quiz drafts ready" if len(waiting) == 2
                 else f"{waiting[0].capitalize()} draft ready to review")
    else:
        title = f"{', '.join(name.capitalize() for name in missing)} draft missing"

    lines = []
    for name in ("poll", "quiz"):
        task = tasks[name]
        if task["waiting"]:
            lines.append(f"{name.capitalize()}: {task['summary']}")
        elif not task["exists"]:
            lines.append(f"{name.capitalize()}: not generated")
    return title, " · ".join(lines)


async def notify_admin_reviews_ready(session: AsyncSession, day: date) -> bool:
    """Send the day's single review push. Returns whether anything was sent.

    Best-effort throughout: a notification failure must never break draft
    generation, which is the job that actually matters.
    """
    # Imported here so the poll/quiz generation path does not depend on the
    # admin page module at import time.
    from app.admin_home import pending_reviews

    if not (settings.ADMIN_USER_EMAIL or settings.ADMIN_ALERT_TOPIC):
        return False
    tasks = await pending_reviews(session, day)
    composed = compose(tasks)
    if composed is None:
        logger.info("Admin review push skipped for %s: nothing waiting", day)
        return False
    title, body = composed
    return await _push_to_admin(session, title, body, settings.ADMIN_REVIEW_URL)


async def notify_admin_breaking_review(session: AsyncSession, new_count: int, refresh_count: int) -> bool:
    """Push for the Breaking slot's human-review queue — see
    backend/docs/breaking-human-review-plan.md. Called from
    app.services.breaking.process_breaking_cycle only when this poll cycle
    actually raised at least one new pending_review candidate or refresh
    review, which is what keeps this from firing every 20 minutes while
    older items just sit waiting — a fast-moving story can still trigger it
    repeatedly (a new refresh review every +5 sources), but never more often
    than genuinely new items appear.

    Best-effort, same posture as notify_admin_reviews_ready: a notification
    failure must never touch the poller's own transaction.
    """
    if not (settings.ADMIN_USER_EMAIL or settings.ADMIN_ALERT_TOPIC or settings.ADMIN_ALERT_EMAIL_TO):
        return False

    parts = []
    if new_count:
        parts.append(f"{new_count} new candidate{'s' if new_count != 1 else ''}")
    if refresh_count:
        parts.append(f"{refresh_count} refresh{'es' if refresh_count != 1 else ''}")
    if not parts:
        return False

    title = f"Breaking review: {' + '.join(parts)}"
    body = "Waiting for a developing-vs-echo call before the LLM narrative pass runs."
    url = f"{settings.ADMIN_REVIEW_URL}/breaking"

    pushed = await _push_to_admin(session, title, body, url)
    emailed = await send_admin_email(title, f"{body}\n\n{url}")
    return pushed or emailed


_MAX_TITLES_IN_BODY = 5


def compose_timeline_generation(
    titles: list[str], rejected: int, failed: int, unfilled: int,
) -> tuple[str, str] | None:
    """(title, body) for the timeline generation push, or None if the cycle
    generated nothing at all.

    `titles` are the coherent narratives written this cycle; `rejected` are
    chains the LLM judged incoherent, `failed` are chains whose generation
    errored, `unfilled` is how many of the tab's slots ended up empty. Every
    chain unchanged since the last cycle costs no LLM call and reports
    nothing — returning None there is what keeps a quiet news day from
    becoming a daily "nothing happened" push.
    """
    if not (titles or rejected or failed):
        return None

    counts = []
    if titles:
        counts.append(f"{len(titles)} generated")
    if failed:
        counts.append(f"{failed} failed")
    if rejected:
        counts.append(f"{rejected} rejected")
    title = f"Timelines: {', '.join(counts)}"

    lines = list(titles[:_MAX_TITLES_IN_BODY])
    if len(titles) > _MAX_TITLES_IN_BODY:
        lines.append(f"+{len(titles) - _MAX_TITLES_IN_BODY} more")
    if rejected:
        lines.append(f"{rejected} rejected as incoherent")
    if unfilled:
        lines.append(f"{unfilled} slot{'s' if unfilled != 1 else ''} unfilled")
    return title, " · ".join(lines)


async def notify_admin_timelines_generated(
    session: AsyncSession, titles: list[str], rejected: int, failed: int, unfilled: int,
) -> bool:
    """Push + email after a timeline generation cycle (scripts/run_timeline_
    scheduler.py), deep-linking to the Timelines admin page. Same channels as
    notify_admin_breaking_review, and the same posture: best-effort, never
    raises — the cycle's own commit has already happened by the time this
    runs, and a notification failure must not be reported as a generation
    failure.
    """
    try:
        if not (settings.ADMIN_USER_EMAIL or settings.ADMIN_ALERT_TOPIC or settings.ADMIN_ALERT_EMAIL_TO):
            return False
        composed = compose_timeline_generation(titles, rejected, failed, unfilled)
        if composed is None:
            logger.info("Timeline generation push skipped: nothing generated this cycle")
            return False
        title, body = composed
        # Imported here, like admin_home in notify_admin_reviews_ready, so the
        # generation path does not depend on the admin page module at import time.
        from app.admin_session import admin_url
        url = admin_url("/admin/timelines")

        pushed = await _push_to_admin(session, title, body, url)
        emailed = await send_admin_email(title, f"{body}\n\n{url}")
        return pushed or emailed
    except Exception as e:  # noqa: BLE001
        logger.warning("Timeline generation notification failed: %s", type(e).__name__)
        return False


async def notify_admin_failure(source: str, error: Exception) -> bool:
    """Email-only crash alert for a currently-silent `except` block. Kept
    email-only (not FCM) since a pipeline failure shouldn't depend on FCM
    itself being healthy. Rate-limited per (source, exception type) so a
    pipeline failing every poll cycle doesn't send dozens of emails a day
    and get filtered to spam — see _FAILURE_COOLDOWN above.

    Best-effort: never raises, never touches the caller's own transaction.
    """
    key = f"{source}:{type(error).__name__}"
    now = datetime.utcnow()
    last = _last_failure_alert.get(key)
    if last is not None and now - last < _FAILURE_COOLDOWN:
        return False
    _last_failure_alert[key] = now

    subject = f"[oin] {source} failed"
    body = f"{type(error).__name__}: {error}"
    return await send_admin_email(subject, body)
