"""
The admin SPA's Announcements (app/admin_announcements.py) and Hot topics
(app/admin_topics.py) APIs.

What matters: nothing reads without a session or writes without the CSRF
header; the form's IST wall-clock times are stored as UTC; validation errors
come back as readable sentences; only live/upcoming rows are listed; and
every add/delete clears the public endpoint's cache so the app sees it now.
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import admin_announcements, admin_topics
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import AdminTopic, Announcement

USERNAME = "reviewer"
PASSWORD = "correct-horse"
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(autouse=True)
def admin_credentials(monkeypatch):
    monkeypatch.setattr(settings, "POLL_ADMIN_USERNAME", USERNAME)
    monkeypatch.setattr(settings, "POLL_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "POLL_SESSION_SECRET", "test-secret")


class FakeRedis:
    def __init__(self):
        self.deleted = []

    async def delete(self, key):
        self.deleted.append(key)


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(admin_announcements, "get_redis_client", lambda: fake)
    monkeypatch.setattr(admin_topics, "get_redis_client", lambda: fake)
    return fake


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (Announcement, AdminTopic):
            await conn.run_sync(model.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.session_factory = Session
        yield c
    app.dependency_overrides.clear()


async def sign_in(client) -> dict:
    r = await client.post("/admin/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    return {"X-CSRF-Token": (await client.get("/admin/api/session")).json()["csrf"]}


def ist(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%Y-%m-%dT%H:%M")


def announcement(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    body = {"kind": "event", "title": "Election night", "body": "Live results", "ctaLabel": "Open",
            "actionType": "story", "actionValue": "46759", "startsAt": ist(now - timedelta(hours=1)),
            "endsAt": ist(now + timedelta(hours=5)), "priority": 3}
    body.update(overrides)
    return body


class TestAnnouncements:
    async def test_needs_session_and_csrf(self, client, redis):
        assert (await client.get("/admin/api/announcements")).status_code == 401
        await sign_in(client)
        assert (await client.post("/admin/api/announcements", json=announcement())).status_code == 403
        assert (await client.delete("/admin/api/announcements/1")).status_code == 403
        assert redis.deleted == []

    async def test_add_list_delete(self, client, redis):
        h = await sign_in(client)
        r = await client.post("/admin/api/announcements", json=announcement(), headers=h)
        assert r.status_code == 200, r.text
        item = r.json()["item"]
        assert item["status"] == "active" and item["actionType"] == "story" and item["ctaLabel"] == "Open"
        assert redis.deleted == ["announcements:active"]

        later = datetime.now(timezone.utc) + timedelta(days=2)
        await client.post("/admin/api/announcements", headers=h, json=announcement(
            title="Offer", kind="offer", actionType="", actionValue="", body="", ctaLabel="",
            startsAt=ist(later), endsAt=ist(later + timedelta(hours=1))))

        d = (await client.get("/admin/api/announcements")).json()
        assert [(i["title"], i["status"]) for i in d["items"]] == [("Election night", "active"), ("Offer", "upcoming")]
        offer = d["items"][1]
        assert offer["actionType"] is None and offer["body"] is None and offer["ctaLabel"] is None
        assert d["kinds"] == ["event", "offer", "info"] and d["actionTypes"] == ["url", "story", "paywall"]

        r = await client.delete(f"/admin/api/announcements/{item['id']}", headers=h)
        assert r.status_code == 200
        assert len((await client.get("/admin/api/announcements")).json()["items"]) == 1
        assert (await client.delete(f"/admin/api/announcements/{item['id']}", headers=h)).status_code == 404

    async def test_times_are_ist_stored_as_utc(self, client, redis):
        h = await sign_in(client)
        await client.post("/admin/api/announcements", headers=h,
                          json=announcement(startsAt="2030-01-01T10:00", endsAt="2030-01-01T12:30"))
        async with client.session_factory() as s:
            row = await s.scalar(select(Announcement))
        assert row.starts_at.replace(tzinfo=timezone.utc) == datetime(2030, 1, 1, 4, 30, tzinfo=timezone.utc)
        assert row.ends_at.replace(tzinfo=timezone.utc) == datetime(2030, 1, 1, 7, 0, tzinfo=timezone.utc)

    async def test_expired_rows_are_not_listed(self, client):
        async with client.session_factory() as s:
            now = datetime.now(timezone.utc)
            s.add(Announcement(kind="info", title="Old", starts_at=now - timedelta(days=3), ends_at=now - timedelta(days=2)))
            await s.commit()
        await sign_in(client)
        assert (await client.get("/admin/api/announcements")).json()["items"] == []

    @pytest.mark.parametrize("override, words", [
        ({"kind": "promo"}, "Kind"),
        ({"title": "  "}, "title"),
        ({"title": "x" * 121}, "120"),
        ({"actionType": "deeplink"}, "Action"),
        ({"startsAt": "tomorrow"}, "Start"),
        ({"startsAt": "2030-01-02T10:00", "endsAt": "2030-01-01T10:00"}, "after the start"),
    ])
    async def test_validation_reads_as_a_sentence(self, client, redis, override, words):
        h = await sign_in(client)
        r = await client.post("/admin/api/announcements", json=announcement(**override), headers=h)
        assert r.status_code == 400 and words in r.json()["detail"]
        assert redis.deleted == []


class TestTopics:
    async def test_needs_session_and_csrf(self, client):
        assert (await client.get("/admin/api/topics")).status_code == 401
        await sign_in(client)
        assert (await client.post("/admin/api/topics", json={"word": "x", "date": "2030-01-01"})).status_code == 403

    async def test_add_list_in_order_and_delete(self, client, redis, monkeypatch):
        monkeypatch.setattr(admin_topics, "india_today", lambda: date(2026, 10, 3))
        async with client.session_factory() as s:
            s.add(AdminTopic(topic_date=date(2026, 10, 2), word="Yesterday", display_order=0))
            await s.commit()
        h = await sign_in(client)
        for word, day, order in (("Cricket", "2026-10-03", 2), ("Elections", "2026-10-03", 1), ("Budget", "2026-10-04", 0)):
            r = await client.post("/admin/api/topics", json={"word": f" {word} ", "date": day, "order": order}, headers=h)
            assert r.status_code == 200, r.text
        assert redis.deleted == ["topics:active"] * 3

        d = (await client.get("/admin/api/topics")).json()
        assert d["today"] == "2026-10-03"
        assert [(t["word"], t["date"], t["order"]) for t in d["items"]] == [
            ("Elections", "2026-10-03", 1), ("Cricket", "2026-10-03", 2), ("Budget", "2026-10-04", 0)]

        r = await client.delete(f"/admin/api/topics/{d['items'][0]['id']}", headers=h)
        assert r.status_code == 200
        assert [t["word"] for t in (await client.get("/admin/api/topics")).json()["items"]] == ["Cricket", "Budget"]

    @pytest.mark.parametrize("body, words", [
        ({"word": " ", "date": "2030-01-01"}, "word"),
        ({"word": "x" * 61, "date": "2030-01-01"}, "60"),
        ({"word": "Elections", "date": "soon"}, "date"),
    ])
    async def test_validation_reads_as_a_sentence(self, client, redis, body, words):
        h = await sign_in(client)
        r = await client.post("/admin/api/topics", json=body, headers=h)
        assert r.status_code == 400 and words in r.json()["detail"]
