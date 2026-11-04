"""
GET /api/v1/teaser: the one timeline and one explainer the public website shows.

What matters: it returns exactly one of each (never a list, so the website has nothing else to
show), in the same order as the full lists (top tracked timeline, newest explainer), skips drafts and
incoherent timelines, and is empty rather than failing when there is nothing to show.
"""
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app, limiter
from app.models import Article, Explainer, Source, StoryCluster, StoryTimelineFeature

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env():
    limiter.enabled = False           # the limiter's counters live in Redis; not what is under test
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (Source, StoryCluster, Article, StoryTimelineFeature, Explainer):
            await conn.run_sync(model.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, Session
    app.dependency_overrides.pop(get_db, None)
    limiter.enabled = True
    await engine.dispose()


async def _cluster(session, cid):
    session.add(StoryCluster(id=cid, headline=f"Story {cid}", article_count=1, first_seen_at=NOW, last_updated_at=NOW))


async def _timeline(session, tid, title, minutes_ago, **kw):
    await _cluster(session, tid)
    session.add(StoryTimelineFeature(
        id=tid, anchor_cluster_id=tid, title=title, context="ctx", cluster_ids=[tid],
        beats=[{"date_label": "1 Nov", "label": "a", "narration": "n", "cluster_ids": [tid]}],
        coherent=kw.pop("coherent", True), last_seen_in_top=kw.pop("last_seen_in_top", True),
        narrative_generated_at=NOW - timedelta(minutes=minutes_ago), **kw))


def _explainer(eid, question, minutes_ago, status="published"):
    return Explainer(id=eid, question=question, category="national", status=status, quick_answer="Because.",
                     sections=[], sources=[], published_at=NOW - timedelta(minutes=minutes_ago))


async def test_empty_site_returns_nulls_not_an_error(env):
    client, _ = env
    r = await client.get("/api/v1/teaser")
    assert r.status_code == 200
    assert r.json() == {"timeline": None, "explainer": None}


async def test_returns_the_top_timeline_and_the_newest_explainer_only(env):
    client, Session = env
    async with Session() as s:
        await _timeline(s, 1, "Older timeline", minutes_ago=600)
        await _timeline(s, 2, "Newest timeline", minutes_ago=5)
        await _timeline(s, 3, "Middle timeline", minutes_ago=120)
        s.add_all([_explainer(1, "Old question?", 900), _explainer(2, "New question?", 10), _explainer(3, "Mid question?", 300)])
        await s.commit()
    body = (await client.get("/api/v1/teaser")).json()
    assert set(body) == {"timeline", "explainer"}                # two single items, no lists
    assert body["timeline"]["title"] == "Newest timeline"
    assert body["explainer"]["question"] == "New question?"
    assert isinstance(body["timeline"], dict) and isinstance(body["explainer"], dict)


async def test_skips_drafts_and_timelines_that_are_not_tracked_or_coherent(env):
    client, Session = env
    async with Session() as s:
        await _timeline(s, 1, "Not coherent", minutes_ago=1, coherent=False)
        await _timeline(s, 2, "No longer tracked", minutes_ago=2, last_seen_in_top=False)
        await _timeline(s, 3, "The one", minutes_ago=3)
        s.add_all([_explainer(1, "Draft question?", 1, status="draft"), _explainer(2, "Published question?", 50)])
        await s.commit()
    body = (await client.get("/api/v1/teaser")).json()
    assert body["timeline"]["title"] == "The one"
    assert body["explainer"]["question"] == "Published question?"
