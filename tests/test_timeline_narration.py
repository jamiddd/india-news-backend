"""
On-demand timeline narration: the shared service (app/services/
timeline_narration.py) and the admin page's "Generate narration" button
(app/admin_timelines.py).

The service is the part that costs money and touches production data, so the
tests pin its safety properties: a row is only written when the whole run
succeeds, a run that overlapped a narrative regeneration is rejected, and a
row already being narrated is not started twice. The admin tests cover auth/
CSRF, the states the button can be in, and that clicking it schedules the run
instead of blocking the request.
"""
import contextlib

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import get_db
from app.main import app
from app.models import StoryTimelineFeature
from app.services import timeline_narration as tn
from app.services.timeline_audio import SCRIPT_VERSION
from app.services.timeline_narrative import TimelineNarrativeError

USERNAME = "reviewer"
PASSWORD = "correct-horse"

BEATS = [
    {"date_label": "Sept 3", "label": "First", "narration": "Something happened."},
    {"date_label": "Sept 4", "label": "Second", "narration": "Then something else."},
]
SPOKEN = {"intro": "Hi", "beats": ["a", "b"], "closing": "Bye", "version": SCRIPT_VERSION}
AUDIO = {
    "audio_url": "https://cdn.example/1-abc.m4a",
    "audio_duration_seconds": 125,
    "audio_beat_offsets": [3.0, 60.0],
    "spoken_script_hash": "abc",
}


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(StoryTimelineFeature.__table__.create)
    return async_sessionmaker(engine, expire_on_commit=False)


async def add_row(factory, **overrides):
    values = dict(anchor_cluster_id=1, coherent=True, title="A story", context="Some context.",
                  beats=BEATS, last_seen_in_top=True)
    values.update(overrides)
    async with factory() as s:
        row = StoryTimelineFeature(**values)
        s.add(row)
        await s.commit()
        return row.id


async def load(factory, row_id):
    async with factory() as s:
        return await s.get(StoryTimelineFeature, row_id)


# --- the service ----------------------------------------------------------


@pytest.fixture
def service(monkeypatch, session_factory):
    """Service wired to the in-memory DB with Claude, Sarvam, the lease and
    Redis status recording replaced. `calls` records what was invoked."""
    calls = {"claude": 0, "audio": 0, "statuses": []}
    state = {"lease": True, "claude": SPOKEN, "audio": AUDIO}

    async def fake_claude(context, beats):
        calls["claude"] += 1
        if isinstance(state["claude"], Exception):
            raise state["claude"]
        return state["claude"]

    async def fake_audio(anchor, script):
        calls["audio"] += 1
        return state["audio"]

    @contextlib.asynccontextmanager
    async def fake_lease(name, ttl_seconds=180, heartbeat_seconds=45):
        yield state["lease"]

    async def fake_set_status(row_id, status, message=""):
        calls["statuses"].append((row_id, status, message))

    monkeypatch.setattr(tn, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(tn, "call_claude_spoken_script_only", fake_claude)
    monkeypatch.setattr(tn, "generate_audio", fake_audio)
    monkeypatch.setattr(tn, "job_lease", fake_lease)
    monkeypatch.setattr(tn, "set_status", fake_set_status)
    monkeypatch.setattr(tn, "is_configured", lambda: True)
    return calls, state


async def test_a_successful_run_stores_script_and_audio_on_the_row(service, session_factory):
    calls, _ = service
    row_id = await add_row(session_factory, audio_url="https://old.example/old.m4a")

    outcome = await tn.narrate_row(row_id)

    assert outcome.ok and "2:05" in outcome.message
    row = await load(session_factory, row_id)
    assert row.spoken_script == SPOKEN
    assert row.audio_url == AUDIO["audio_url"]
    assert row.audio_duration_seconds == 125
    assert row.audio_beat_offsets == [3.0, 60.0]
    assert row.spoken_script_hash == "abc"
    assert row.audio_generated_at is not None
    assert [s[1] for s in calls["statuses"]] == ["running", "done"]


async def test_a_claude_failure_leaves_the_row_untouched(service, session_factory):
    calls, state = service
    state["claude"] = TimelineNarrativeError("bad json")
    row_id = await add_row(session_factory, audio_url="https://old.example/old.m4a")

    outcome = await tn.narrate_row(row_id)

    assert not outcome.ok and "spoken script" in outcome.message
    assert calls["audio"] == 0  # no Sarvam spend after Claude failed
    row = await load(session_factory, row_id)
    assert row.audio_url == "https://old.example/old.m4a" and row.spoken_script is None
    assert calls["statuses"][-1][1] == "failed"


async def test_an_audio_failure_leaves_the_row_untouched(service, session_factory):
    _, state = service
    state["audio"] = None
    row_id = await add_row(session_factory, audio_url="https://old.example/old.m4a")

    outcome = await tn.narrate_row(row_id)

    assert not outcome.ok
    row = await load(session_factory, row_id)
    assert row.audio_url == "https://old.example/old.m4a"
    assert row.spoken_script is None  # the new script isn't saved without its audio


async def test_a_run_that_overlapped_a_regeneration_is_rejected(service, session_factory, monkeypatch):
    row_id = await add_row(session_factory)

    async def claude_then_regenerate(context, beats):
        async with session_factory() as s:  # the nightly narrator rewrites the beats mid-run
            row = await s.get(StoryTimelineFeature, row_id)
            row.beats = [{"date_label": "Sept 9", "label": "New", "narration": "Rewritten."}]
            await s.commit()
        return SPOKEN

    monkeypatch.setattr(tn, "call_claude_spoken_script_only", claude_then_regenerate)

    outcome = await tn.narrate_row(row_id)

    assert not outcome.ok and "regenerated" in outcome.message
    assert (await load(session_factory, row_id)).audio_url is None


async def test_a_row_already_being_narrated_is_not_started_twice(service, session_factory):
    calls, state = service
    state["lease"] = False
    row_id = await add_row(session_factory)

    outcome = await tn.narrate_row(row_id)

    assert not outcome.ok and "already" in outcome.message
    assert calls["claude"] == 0 and calls["audio"] == 0
    assert calls["statuses"] == []  # the run that owns the row keeps its status


@pytest.mark.parametrize("overrides,reason", [
    ({"coherent": False}, "coherent"),
    ({"context": None}, "context"),
    ({"beats": None}, "context"),
])
async def test_rows_that_cannot_be_narrated_are_refused_before_any_spend(service, session_factory, overrides, reason):
    calls, _ = service
    row_id = await add_row(session_factory, **overrides)
    outcome = await tn.narrate_row(row_id)
    assert not outcome.ok and reason in outcome.message
    assert calls["claude"] == 0 and calls["audio"] == 0


async def test_an_unconfigured_server_fails_fast(service, session_factory, monkeypatch):
    calls, _ = service
    monkeypatch.setattr(tn, "is_configured", lambda: False)
    row_id = await add_row(session_factory)
    outcome = await tn.narrate_row(row_id)
    assert not outcome.ok and "not configured" in outcome.message
    assert calls["claude"] == 0


async def test_an_unexpected_error_is_reported_not_raised(service, session_factory, monkeypatch):
    async def boom(context, beats):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(tn, "call_claude_spoken_script_only", boom)
    row_id = await add_row(session_factory)
    outcome = await tn.narrate_row(row_id)
    assert not outcome.ok and "kaboom" in outcome.message


# --- the admin page -------------------------------------------------------


@pytest.fixture(autouse=True)
def admin_credentials(monkeypatch):
    monkeypatch.setattr(settings, "POLL_ADMIN_USERNAME", USERNAME)
    monkeypatch.setattr(settings, "POLL_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "POLL_SESSION_SECRET", "test-secret")


@pytest.fixture
async def admin(session_factory, monkeypatch):
    """Signed-out client against an in-memory DB, with the narration
    service's Redis/lease/background work replaced. `scheduled` collects the
    row ids the button asked to narrate."""
    scheduled: list[int] = []
    state = {"statuses": {}, "running": False, "configured": True}

    async def fake_statuses(ids):
        return {i: s for i, s in state["statuses"].items() if i in list(ids)}

    async def fake_in_progress(row_id):
        return state["running"]

    async def fake_set_status(row_id, status, message=""):
        state["statuses"][row_id] = {"state": status, "message": message, "at": "2026-09-22T06:00:00+00:00"}

    async def fake_narrate(row_id):
        scheduled.append(row_id)

    monkeypatch.setattr(tn, "get_statuses", fake_statuses)
    monkeypatch.setattr(tn, "in_progress", fake_in_progress)
    monkeypatch.setattr(tn, "set_status", fake_set_status)
    monkeypatch.setattr(tn, "narrate_row", fake_narrate)
    monkeypatch.setattr("app.admin_timelines.is_configured", lambda: state["configured"])

    async def override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.scheduled, client.state = scheduled, state
        yield client
    app.dependency_overrides.clear()


async def sign_in(client):
    r = await client.post("/admin/timelines/login", data={"username": USERNAME, "password": PASSWORD},
                          follow_redirects=False)
    assert r.status_code == 303


def csrf_from(page):
    marker = "name=csrf value='"
    start = page.index(marker) + len(marker)
    return page[start:page.index("'", start)]


async def test_narrate_requires_a_signed_in_session_and_csrf(admin, session_factory):
    row_id = await add_row(session_factory)
    assert (await admin.post("/admin/timelines/narrate", data={"row_id": row_id})).status_code == 403
    await sign_in(admin)
    r = await admin.post("/admin/timelines/narrate", data={"row_id": row_id, "csrf": "wrong"})
    assert r.status_code == 403
    assert admin.scheduled == []


async def test_status_endpoint_requires_sign_in(admin, session_factory):
    row_id = await add_row(session_factory)
    assert (await admin.get(f"/admin/timelines/narrate/{row_id}")).status_code == 401


async def test_the_button_schedules_the_run_and_returns_immediately(admin, session_factory):
    row_id = await add_row(session_factory)
    await sign_in(admin)
    csrf = csrf_from((await admin.get("/admin/timelines")).text)

    r = await admin.post("/admin/timelines/narrate", data={"row_id": row_id, "csrf": csrf},
                         follow_redirects=False)

    assert r.status_code == 303 and r.headers["location"] == f"/admin/timelines#row-{row_id}"
    assert admin.scheduled == [row_id]
    assert admin.state["statuses"][row_id]["state"] == "running"  # visible on the redirected page


async def test_a_second_click_while_running_does_not_start_another(admin, session_factory):
    row_id = await add_row(session_factory)
    await sign_in(admin)
    csrf = csrf_from((await admin.get("/admin/timelines")).text)
    admin.state["running"] = True

    r = await admin.post("/admin/timelines/narrate", data={"row_id": row_id, "csrf": csrf},
                         follow_redirects=False)

    assert r.headers["location"].endswith("notice=already-running")
    assert admin.scheduled == []


async def test_ineligible_and_unconfigured_rows_are_refused_with_a_notice(admin, session_factory):
    bad = await add_row(session_factory, anchor_cluster_id=2, coherent=False)
    good = await add_row(session_factory, anchor_cluster_id=3)
    await sign_in(admin)
    csrf = csrf_from((await admin.get("/admin/timelines")).text)

    r = await admin.post("/admin/timelines/narrate", data={"row_id": bad, "csrf": csrf}, follow_redirects=False)
    assert r.headers["location"].endswith("notice=not-eligible")

    admin.state["configured"] = False
    r = await admin.post("/admin/timelines/narrate", data={"row_id": good, "csrf": csrf}, follow_redirects=False)
    assert r.headers["location"].endswith("notice=not-configured")
    assert admin.scheduled == []


async def test_unknown_row_is_a_404(admin, session_factory):
    await add_row(session_factory)  # gives the page a form to take the CSRF token from
    await sign_in(admin)
    csrf = csrf_from((await admin.get("/admin/timelines")).text)
    r = await admin.post("/admin/timelines/narrate", data={"row_id": 9999, "csrf": csrf})
    assert r.status_code == 404
    assert admin.scheduled == []


async def test_dashboard_shows_each_narration_state(admin, session_factory):
    none_id = await add_row(session_factory, anchor_cluster_id=10, title="No audio")
    old_id = await add_row(session_factory, anchor_cluster_id=11, title="Old voice",
                           audio_url="https://cdn/x.m4a", audio_duration_seconds=100,
                           spoken_script={"intro": "x", "beats": ["y"], "style_scene": "legacy"})
    new_id = await add_row(session_factory, anchor_cluster_id=12, title="New voice",
                           audio_url="https://cdn/y.m4a", audio_duration_seconds=305,
                           spoken_script=SPOKEN)
    await add_row(session_factory, anchor_cluster_id=13, title="Not coherent", coherent=False)
    admin.state["statuses"][none_id] = {"state": "failed", "message": "Sarvam/upload failed",
                                        "at": "2026-09-22T06:00:00+00:00"}
    await sign_in(admin)

    page = (await admin.get("/admin/timelines")).text

    assert "No narration audio yet." in page
    assert "Older narration (previous voice)" in page and "1:40" in page
    assert "Current narration" in page and "5:05" in page
    assert "Last run failed:" in page and "Sarvam/upload failed" in page
    assert page.count("Generate narration") >= 1 and "Regenerate narration" in page
    assert "(not a coherent timeline)" in page  # button disabled with the reason
    assert "location.reload" not in page  # nothing running -> no auto-refresh
    assert new_id and old_id


async def test_dashboard_auto_refreshes_while_a_run_is_in_flight(admin, session_factory):
    row_id = await add_row(session_factory)
    admin.state["statuses"][row_id] = {"state": "running", "message": "", "at": "2026-09-22T06:00:00+00:00"}
    await sign_in(admin)
    page = (await admin.get("/admin/timelines")).text
    assert "Generating" in page and "location.reload" in page
    assert "<button disabled>Generating" in page


async def test_status_endpoint_reports_state_and_audio(admin, session_factory):
    row_id = await add_row(session_factory, audio_url="https://cdn/y.m4a", spoken_script=SPOKEN)
    admin.state["statuses"][row_id] = {"state": "done", "message": "narration saved (5:05)",
                                       "at": "2026-09-22T06:00:00+00:00"}
    await sign_in(admin)
    body = (await admin.get(f"/admin/timelines/narrate/{row_id}")).json()
    assert body["state"] == "done" and body["has_audio"] and body["audio_is_current"]
    assert (await admin.get("/admin/timelines/narrate/9999")).status_code == 404
