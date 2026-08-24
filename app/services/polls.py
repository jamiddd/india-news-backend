import hashlib
import hmac
import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from sqlalchemy import delete, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Article, DailyPoll, DeviceToken, PollFallback, PollOption, PollVote, Source, StoryCluster, User, utc_now
from app.services.enrichment import parse_json_response

IST = ZoneInfo("Asia/Kolkata")
UNSAFE = re.compile(r"\b(killed|dies|death|rape|murder|lynch|riot|communal|terror|suicide|victim|accident|crash|flood|cyclone|earthquake|funeral|hostage)\b", re.I)

FALLBACKS = [
    ("Should public transport receive greater priority in city planning?", "A general question about improving everyday urban mobility.", ["Yes", "Only in larger cities", "No", "Need more information"]),
    ("Should financial literacy be a required subject in schools?", "Financial decisions become part of adult life soon after school.", ["Yes", "Offer it as an elective", "No"]),
    ("Should government services remain available both online and offline?", "Digital access is growing, but it is not equally available everywhere.", ["Yes, both are necessary", "Mostly online", "Mostly offline"]),
    ("Should cities create more car-free public areas?", "Pedestrian-only areas can change mobility, commerce and public space.", ["Yes", "Only in selected areas", "No"]),
    ("Should schools spend more time teaching practical life skills?", "Practical skills can include communication, first aid and basic finance.", ["Yes", "The current balance is adequate", "No"]),
    ("Should remote work remain an option where the job permits it?", "Flexible work affects commuting, collaboration and family routines.", ["Yes", "Use a hybrid model", "No"]),
    ("Should single-use plastic rules be made stricter?", "Plastic waste remains a visible environmental and civic challenge.", ["Yes", "Improve enforcement first", "No"]),
    ("Should public libraries receive more local funding?", "Libraries provide books, study space and access to information.", ["Yes", "Keep funding unchanged", "No"]),
    ("Should basic first-aid training be offered in every secondary school?", "Early training can help people respond during common emergencies.", ["Yes", "Make it optional", "No"]),
    ("Should large events be required to provide convenient public transport access?", "Major gatherings can create congestion and parking pressure.", ["Yes", "Only above a size threshold", "No"]),
    ("Should food labels use simpler front-of-pack nutrition information?", "Shoppers often make decisions quickly and compare many products.", ["Yes", "Current labels are sufficient", "No"]),
    ("Should local governments publish spending data in simpler formats?", "Public budgets can be difficult for non-specialists to understand.", ["Yes", "Only major projects", "No"]),
    ("Should community service be encouraged as part of higher education?", "Community work can connect students with local needs.", ["Yes", "Only as an elective", "No"]),
    ("Should repairability scores be displayed on electronic products?", "Repair information may affect cost, waste and purchase decisions.", ["Yes", "Only for expensive products", "No"]),
]


def poll_times(day: date) -> tuple[datetime, datetime]:
    publish = datetime.combine(day, time(9), tzinfo=IST)
    return publish, publish + timedelta(days=1)


async def seed_fallbacks(session: AsyncSession) -> None:
    count = await session.scalar(select(func.count(PollFallback.id)))
    if count:
        return
    session.add_all(PollFallback(question=q, context=c, options=o) for q, c, o in FALLBACKS)
    await session.commit()


def validate_draft(data: dict) -> tuple[str, str, list[str]]:
    question = str(data.get("question") or "").strip()
    context = str(data.get("context") or "").strip()
    options = [str(value).strip() for value in data.get("options") or []]
    if not 20 <= len(question) <= 180 or not question.endswith("?"):
        raise ValueError("Invalid poll question")
    if not 20 <= len(context) <= 300:
        raise ValueError("Invalid poll context")
    if not 2 <= len(options) <= 4 or len({value.casefold() for value in options}) != len(options):
        raise ValueError("Poll needs 2-4 distinct options")
    if any(not value or len(value) > 80 for value in options) or UNSAFE.search(question):
        raise ValueError("Unsafe or invalid poll output")
    return question, context, options


async def generate_draft(session: AsyncSession, poll_date: date, replace: bool = False) -> DailyPoll:
    existing = await session.scalar(select(DailyPoll).where(DailyPoll.poll_date == poll_date))
    if existing and not replace:
        return existing
    if existing and existing.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be regenerated")
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")

    cutoff = utc_now() - timedelta(hours=24)
    candidates = (await session.execute(
        select(StoryCluster).options(selectinload(StoryCluster.articles).selectinload(Article.source))
        .where(StoryCluster.first_seen_at >= cutoff, StoryCluster.distinct_source_count >= 2)
        .order_by(desc(StoryCluster.headline_score)).limit(20)
    )).scalars().all()
    safe = [cluster for cluster in candidates if not UNSAFE.search(cluster.headline or "")][:8]
    if not safe:
        raise RuntimeError("No safe corroborated stories available")
    payload = [{
        "cluster_id": cluster.id,
        "headline": cluster.headline,
        "summary": cluster.summary,
        "articles": [{"outlet": a.source.name if a.source else "Source", "headline": a.title, "snippet": (a.snippet or "")[:220]} for a in cluster.articles[:5]],
    } for cluster in safe]
    system = """You draft a neutral daily public-opinion poll for an Indian news app. Use only supplied reporting. Choose one suitable policy, civic, economic, science, technology, education, environment or public-service issue. Never poll on a person's guilt, tragedy, death, communal identity, religion, caste, active crime, or unverifiable claim. Return only JSON: {\"source_cluster_id\": integer, \"question\": string ending ?, \"context\": one neutral factual sentence, \"options\": 2-4 mutually exclusive balanced strings}. Avoid loaded premises and include nuance when a binary choice is misleading."""
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post("https://api.anthropic.com/v1/messages", headers={
            "x-api-key": settings.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"
        }, json={"model": "claude-haiku-4-5", "max_tokens": 700, "system": system, "messages": [{"role": "user", "content": json.dumps(payload)}]})
        response.raise_for_status()
    raw = response.json().get("content") or []
    data = parse_json_response(raw[0]["text"] if raw else "")
    question, context, options = validate_draft(data)
    cluster = next((item for item in safe if item.id == int(data.get("source_cluster_id", 0))), None)
    if cluster is None:
        raise ValueError("AI selected an unknown source cluster")
    publish, closes = poll_times(poll_date)
    if existing:
        existing.question, existing.context = question, context
        existing.source_cluster_id, existing.source_headline = cluster.id, cluster.headline
        # Bug fix: this update-in-place path previously left generation_method
        # untouched, so a row first created as a "fallback" (e.g. the scheduler
        # auto-published a fallback because no draft was ready in time) and
        # later regenerated here with real AI content kept showing
        # generation_method="fallback" — wrong for admin/audit purposes even
        # though the actual question/context/options were genuinely AI-drafted.
        existing.generation_method = "ai"
        await session.execute(delete(PollOption).where(PollOption.poll_id == existing.id))
        await session.flush()
        poll = existing
    else:
        poll = DailyPoll(poll_date=poll_date, question=question, context=context, status="draft", source_cluster_id=cluster.id, source_headline=cluster.headline, generation_method="ai", publish_at=publish, closes_at=closes)
        session.add(poll)
        await session.flush()
    session.add_all(PollOption(poll_id=poll.id, position=i, text=value) for i, value in enumerate(options))
    await session.commit()
    await session.refresh(poll)
    return poll


async def notify_admin_draft_ready(session: AsyncSession, poll: DailyPoll) -> None:
    """Pushes a data-only FCM message to every device registered to
    ADMIN_USER_EMAIL, deep-linking to the admin review page — see
    NewsFirebaseMessagingService.onMessageReceived's "url" handling on the
    client, which opens it in Chrome instead of the app. Best-effort: any
    failure here must never break draft generation itself."""
    if not settings.ADMIN_USER_EMAIL:
        return
    try:
        from firebase_admin import messaging
        from app.services.firebase_auth import _get_firebase_app
        app = _get_firebase_app()
    except Exception:
        return

    admin = await session.scalar(select(User).where(User.email == settings.ADMIN_USER_EMAIL))
    if not admin:
        return
    tokens = (await session.execute(select(DeviceToken).where(DeviceToken.user_id == admin.id))).scalars().all()
    for device in tokens:
        try:
            messaging.send(messaging.Message(
                data={
                    "title": "Daily poll draft ready",
                    "body": poll.question,
                    "url": settings.ADMIN_POLL_REVIEW_URL,
                    "channel_id": "admin_alerts",
                },
                android=messaging.AndroidConfig(priority="high"),
                token=device.fcm_token,
            ), app=app)
        except Exception:
            # Same dead-token/permission failures scripts/send_notifications.py
            # tolerates — one admin device failing to receive this shouldn't
            # block generation or the other device.
            continue


async def approve_poll(session: AsyncSession, poll_id: int, question: str, context: str, options: list[str]) -> None:
    poll = await session.get(DailyPoll, poll_id)
    if not poll or poll.status != "draft" or datetime.now(IST) >= poll.publish_at:
        raise HTTPException(status_code=409, detail="This draft can no longer be approved")
    question, context, options = validate_draft({"question": question, "context": context, "options": options})
    poll.question, poll.context, poll.status, poll.approved_at = question, context, "scheduled", utc_now()
    await session.execute(delete(PollOption).where(PollOption.poll_id == poll.id))
    session.add_all(PollOption(poll_id=poll.id, position=i, text=value) for i, value in enumerate(options))
    await session.commit()


async def activate_poll(session: AsyncSession, poll_date: date) -> DailyPoll:
    await seed_fallbacks(session)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 790000000 + poll_date.toordinal()})
    poll = await session.scalar(select(DailyPoll).where(DailyPoll.poll_date == poll_date))
    if poll and poll.status == "active":
        return poll
    if poll and poll.status == "scheduled":
        poll.status = "active"
    else:
        fallback = await session.scalar(select(PollFallback).where(PollFallback.active.is_(True)).order_by(PollFallback.last_used_at.asc().nullsfirst(), PollFallback.id))
        if not fallback:
            raise RuntimeError("No active fallback polls")
        publish, closes = poll_times(poll_date)
        if poll:
            poll.question, poll.context, poll.status = fallback.question, fallback.context, "active"
            poll.source_cluster_id, poll.source_headline, poll.generation_method = None, None, "fallback"
            poll.publish_at, poll.closes_at = publish, closes
            await session.execute(delete(PollOption).where(PollOption.poll_id == poll.id))
            await session.flush()
        else:
            poll = DailyPoll(poll_date=poll_date, question=fallback.question, context=fallback.context, status="active", generation_method="fallback", publish_at=publish, closes_at=closes)
            session.add(poll); await session.flush()
        session.add_all(PollOption(poll_id=poll.id, position=i, text=value) for i, value in enumerate(fallback.options))
        fallback.last_used_at = utc_now()
    previous = (await session.execute(select(DailyPoll).where(DailyPoll.status == "active", DailyPoll.id != poll.id))).scalars().all()
    for old in previous: old.status = "closed"
    await session.commit(); await session.refresh(poll)
    return poll


def voter_hash(installation_id: str) -> str:
    if not settings.POLL_VOTER_HASH_SECRET:
        raise HTTPException(status_code=503, detail="Poll voting is not configured")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", installation_id):
        raise HTTPException(status_code=422, detail="Invalid installation identifier")
    return hmac.new(settings.POLL_VOTER_HASH_SECRET.encode(), installation_id.encode(), hashlib.sha256).hexdigest()


async def serialize_poll(session: AsyncSession, poll: DailyPoll, hashed_voter: str | None) -> dict:
    options = (await session.execute(select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.position))).scalars().all()
    vote = await session.scalar(select(PollVote).where(PollVote.poll_id == poll.id, PollVote.voter_hash == hashed_voter)) if hashed_voter else None
    counts = {}
    total = None
    if vote:
        rows = await session.execute(select(PollVote.option_id, func.count(PollVote.id)).where(PollVote.poll_id == poll.id).group_by(PollVote.option_id))
        counts = dict(rows.all()); total = sum(counts.values())
    return {"id": poll.id, "date": poll.poll_date, "question": poll.question, "context": poll.context, "source_cluster_id": poll.source_cluster_id, "source_headline": poll.source_headline, "closes_at": poll.closes_at, "selected_option_id": vote.option_id if vote else None, "total_votes": total, "options": [{"id": o.id, "text": o.text, "votes": counts.get(o.id) if vote else None, "percentage": round(counts.get(o.id, 0) * 100 / total, 1) if vote and total else None} for o in options]}
