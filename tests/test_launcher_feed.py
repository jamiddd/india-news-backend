from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.launcher_schemas import LauncherFeedOut, LauncherStoryItem
from app.services.launcher_feed import (
    CONTINUATION_PROMO_ID,
    GAME_TYPES,
    game_for_date,
    game_tile,
    apply_promos,
    build_teaser,
    cluster_to_story_item,
    continuation_promo,
    insert_promos,
    promos_allowed,
    decode_cursor,
    display_tag,
    encode_cursor,
    pick_primary_article,
)

NOW = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)


def art(id, source="The Hindu", category="national", image="https://img/x.jpg",
        age_min=0, width=None, height=None):
    return SimpleNamespace(
        id=id,
        title=f"title {id}",
        image_url=image,
        image_width=width,
        image_height=height,
        published_at=NOW - timedelta(minutes=age_min),
        source=SimpleNamespace(name=source, category=category),
    )


def cluster(articles, id=7, headline="Headline"):
    return SimpleNamespace(id=id, headline=headline, articles=articles)


def test_primary_prefers_non_pib_article_with_image():
    pib = art(1, source="PIB", image="https://img/pib.jpg", age_min=0)
    other = art(2, source="NDTV", image="https://img/ndtv.jpg", age_min=30)
    assert pick_primary_article([pib, other]).id == 2


def test_primary_prefers_hd_then_newest():
    sd_new = art(1, age_min=1, width=400, height=300)
    hd_old = art(2, age_min=60, width=1600, height=900)
    assert pick_primary_article([sd_new, hd_old]).id == 2


def test_primary_falls_back_to_newest_without_images():
    a = art(1, image=None, age_min=50)
    b = art(2, image=None, age_min=5)
    assert pick_primary_article([a, b]).id == 2


def test_primary_none_for_empty():
    assert pick_primary_article([]) is None


def test_story_item_shape_has_no_publisher_url_or_body():
    item = cluster_to_story_item(cluster([art(1, source="NDTV", category="business")], id=42))
    assert item["id"] == "c_42" and item["cluster_id"] == 42
    assert item["tag"] == "Business" and item["source_name"] == "NDTV"
    assert item["title"] == "Headline"
    assert not {"url", "content", "snippet", "video_url", "articles"} & set(item)
    # And it round-trips through the response model.
    LauncherStoryItem(**item)


def test_story_item_none_for_cluster_without_articles():
    assert cluster_to_story_item(cluster([])) is None


def test_story_item_uses_primary_article_for_source_and_image():
    pib = art(1, source="PIB", image="https://img/pib.jpg")
    ndtv = art(2, source="NDTV", image="https://img/ndtv.jpg")
    item = cluster_to_story_item(cluster([pib, ndtv]))
    assert item["source_name"] == "NDTV" and item["image_url"] == "https://img/ndtv.jpg"


def test_display_tag():
    assert display_tag("northeast") == "Northeast"
    assert display_tag("general") == "News"
    assert display_tag(None) == "News"
    assert display_tag("world_affairs") == "World Affairs"


def test_cursor_roundtrip_and_malformed():
    assert decode_cursor(encode_cursor(2.4571, 99)) == (2.4571, 99)
    assert decode_cursor(None) is None
    assert decode_cursor("") is None
    assert decode_cursor("garbage") is None
    assert decode_cursor("abc:def") is None
    assert decode_cursor("12345") is None  # old bare-int style: start over


def test_feed_out_serializes_and_parses_from_cache_json():
    item = cluster_to_story_item(cluster([art(1)]))
    out = LauncherFeedOut(items=[item], next_cursor=None, has_more=False, generated_at=NOW)
    again = LauncherFeedOut.model_validate_json(out.model_dump_json())
    assert again.items[0].cluster_id == 7 and again.has_more is False


# --- route-level tests: the router mounted on a bare FastAPI app -------------
# main.py needs Python 3.10+ and a database, so the router is exercised on its
# own with a fake session and a fake cache. The fake session records the
# statement so the SQL itself can be compiled and inspected.

from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.dialects import postgresql

import app.launcher_feed as lf
from app.database import get_db


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(list(self.rows))


def _cl(id, score):
    c = cluster([art(id, source="NDTV")], id=id, headline=f"H{id}")
    c.headline_score = score
    return c


@pytest.fixture
def client_and_session(monkeypatch):
    store = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ttl):
        store[key] = value

    monkeypatch.setattr(lf, "india_today", lambda: date(2026, 10, 27))
    monkeypatch.setattr(lf, "_cache_get", fake_get)
    monkeypatch.setattr(lf, "_cache_set", fake_set)

    session = _FakeSession([])
    app = FastAPI()
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(lf.create_launcher_feed_router(limiter))

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), session, store


def test_route_returns_page_with_cursor_when_more(client_and_session):
    client, session, _ = client_and_session
    session.rows = [_cl(10, 3.0), _cl(9, 2.0), _cl(8, 1.0)]  # limit=2 -> has_more
    body = client.get("/api/v1/launcher/feed", params={"limit": 2}).json()
    assert [i["cluster_id"] for i in body["items"]] == [10, 9]
    assert body["has_more"] is True
    assert decode_cursor(body["next_cursor"]) == (2.0, 9)
    assert body["items"][0]["type"] == "story"


def test_route_last_page_has_no_cursor(client_and_session):
    client, session, _ = client_and_session
    session.rows = [_cl(10, 3.0)]
    body = client.get("/api/v1/launcher/feed").json()
    assert body["has_more"] is False and body["next_cursor"] is None


def test_route_skips_cluster_without_articles_and_empty_feed(client_and_session):
    client, session, _ = client_and_session
    empty = _cl(5, 1.0)
    empty.articles = []
    session.rows = [empty]
    assert client.get("/api/v1/launcher/feed").json()["items"] == []


def test_route_second_request_served_from_cache(client_and_session):
    client, session, store = client_and_session
    session.rows = [_cl(10, 3.0)]
    first = client.get("/api/v1/launcher/feed").json()
    session.rows = [_cl(99, 9.0)]  # DB changed; cache must win
    second = client.get("/api/v1/launcher/feed").json()
    assert first == second and len(session.statements) == 1 and store


def test_route_limit_bounds(client_and_session):
    client, _, _ = client_and_session
    assert client.get("/api/v1/launcher/feed", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/launcher/feed", params={"limit": 31}).status_code == 422


def test_route_sql_orders_by_score_and_applies_cursor(client_and_session):
    client, session, _ = client_and_session
    client.get("/api/v1/launcher/feed", params={"cursor": encode_cursor(2.5, 40)})
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "ORDER BY story_clusters.headline_score DESC, story_clusters.id DESC" in sql
    assert "(story_clusters.headline_score, story_clusters.id) <" in sql
    assert "LIMIT" in sql


def test_route_malformed_cursor_starts_over(client_and_session):
    client, session, _ = client_and_session
    assert client.get("/api/v1/launcher/feed", params={"cursor": "junk"}).status_code == 200
    sql = str(session.statements[0].compile(dialect=postgresql.dialect()))
    assert "(story_clusters.headline_score, story_clusters.id) <" not in sql


def test_route_accepts_launcher_headers(client_and_session):
    client, session, _ = client_and_session
    r = client.get("/api/v1/launcher/feed", headers={"X-Client": "spot", "X-Client-Build": "1"})
    assert r.status_code == 200


# --- teaser -------------------------------------------------------------------

FRAMING = [
    {"outlet": "NDTV", "headline_angle": "Leads with the economic impact"},
    {"outlet": "The Hindu", "headline_angle": "Leads with the legal angle"},
    {"outlet": "Mint", "headline_angle": "Focuses on markets"},
]


def tcluster(sources=5, framing=FRAMING, enriched_ago_h=1, category="national", source="NDTV"):
    c = cluster([art(1, source=source, category=category)], id=3)
    c.distinct_source_count = sources
    c.framing_comparison = framing
    c.last_enriched_at = NOW - timedelta(hours=enriched_ago_h) if enriched_ago_h is not None else None
    return c


def test_teaser_picks_an_angle_from_a_different_outlet():
    item = cluster_to_story_item(tcluster(source="NDTV"), NOW)
    assert item["teaser"] == {
        "source_count": 5,
        "angle": {"outlet": "The Hindu", "headline_angle": "Leads with the legal angle"},
    }


def test_teaser_never_exposes_more_than_one_angle():
    teaser = cluster_to_story_item(tcluster(), NOW)["teaser"]
    assert set(teaser) == {"source_count", "angle"} and set(teaser["angle"]) == {"outlet", "headline_angle"}


def test_teaser_needs_three_sources():
    assert cluster_to_story_item(tcluster(sources=2), NOW)["teaser"] is None
    assert cluster_to_story_item(tcluster(sources=3), NOW)["teaser"] is not None


def test_teaser_needs_two_usable_framing_outlets():
    assert build_teaser(tcluster(framing=None), "NDTV", "national", NOW) is None
    assert build_teaser(tcluster(framing=[]), "NDTV", "national", NOW) is None
    assert build_teaser(tcluster(framing=FRAMING[:1]), "NDTV", "national", NOW) is None
    junk = [{"outlet": "A"}, {"headline_angle": "x"}, "str", {"outlet": " ", "headline_angle": "y"}]
    assert build_teaser(tcluster(framing=junk), "NDTV", "national", NOW) is None
    assert build_teaser(tcluster(framing={"not": "a list"}), "NDTV", "national", NOW) is None


def test_teaser_only_for_safe_categories():
    for category in ("national", "world", "business", "Business"):
        assert build_teaser(tcluster(), "NDTV", category, NOW) is not None
    for category in ("politics", "official", "northeast", None, "general"):
        assert build_teaser(tcluster(), "NDTV", category, NOW) is None


def test_teaser_dropped_when_framing_is_stale_but_kept_when_age_unknown():
    assert cluster_to_story_item(tcluster(enriched_ago_h=37), NOW)["teaser"] is None
    assert cluster_to_story_item(tcluster(enriched_ago_h=35), NOW)["teaser"] is not None
    assert cluster_to_story_item(tcluster(enriched_ago_h=None), NOW)["teaser"] is not None


def test_teaser_falls_back_to_card_outlet_if_it_is_the_only_one_with_an_angle():
    only = [{"outlet": "NDTV", "headline_angle": "a"}, {"outlet": "NDTV", "headline_angle": "b"}]
    assert build_teaser(tcluster(framing=only), "NDTV", "national", NOW)["angle"]["headline_angle"] == "a"


# --- promo rules --------------------------------------------------------------

def stories(n):
    return [{"type": "story", "id": f"c_{i}"} for i in range(n)]


def promo(pid="promo_x"):
    p = continuation_promo()
    p["id"] = pid
    return p


def kinds(items):
    return [i["type"][0] for i in items]  # 's' / 'p'


def test_promos_allowed_matrix():
    assert promos_allowed(False, 2) and promos_allowed(False, 30)
    assert not promos_allowed(True, 30)  # News installed
    assert not promos_allowed(False, 1) and not promos_allowed(False, 0)  # too new
    assert not promos_allowed(False, None)  # unknown age


def test_no_promo_when_news_installed():
    out = apply_promos(stories(15), is_first_page=True, news_installed=True, session_age_days=30)
    assert kinds(out) == ["s"] * 15


def test_no_promo_for_young_or_unknown_session():
    for age in (None, 0, 1):
        out = apply_promos(stories(15), is_first_page=True, news_installed=False, session_age_days=age)
        assert "p" not in kinds(out)


def test_continuation_card_at_end_of_first_page():
    out = apply_promos(stories(15), is_first_page=True, news_installed=False, session_age_days=5)
    assert kinds(out) == ["s"] * 15 + ["p"]
    assert out[-1]["id"] == CONTINUATION_PROMO_ID and out[-1]["cta"]["campaign"] == "continue"


def test_no_continuation_card_on_later_pages_or_short_pages():
    later = apply_promos(stories(15), is_first_page=False, news_installed=False, session_age_days=5)
    short = apply_promos(stories(9), is_first_page=True, news_installed=False, session_age_days=5)
    assert "p" not in kinds(later) and "p" not in kinds(short)


def test_dismissed_promo_is_not_served():
    out = apply_promos(stories(15), is_first_page=True, news_installed=False, session_age_days=5,
                       dismissed=[CONTINUATION_PROMO_ID])
    assert "p" not in kinds(out)


def test_insert_promos_clamps_to_min_index():
    out = insert_promos(stories(20), [(0, promo("a"))])
    assert kinds(out).index("p") == 3  # three stories first


def test_insert_promos_enforces_spacing_between_promos():
    out = insert_promos(stories(30), [(3, promo("a")), (9, promo("b")), (11, promo("c")), (20, promo("d"))])
    positions = [i for i, k in enumerate(kinds(out)) if k == "p"]
    story_gaps = [positions[i + 1] - positions[i] - 1 for i in range(len(positions) - 1)]
    assert [out[i]["id"] for i in positions] == ["a", "c", "d"]  # b is 6 stories after a: dropped
    assert all(gap >= 8 for gap in story_gaps)
    assert positions[0] >= 3


def test_insert_promos_does_not_mutate_input_and_preserves_story_order():
    original = stories(12)
    snapshot = [dict(s) for s in original]
    out = insert_promos(original, [(12, promo("a"))])
    assert original == snapshot
    assert [i["id"] for i in out if i["type"] == "story"] == [s["id"] for s in snapshot]


def test_insert_promos_ignores_when_too_few_stories():
    assert kinds(insert_promos(stories(2), [(2, promo("a"))])) == ["s", "s"]


# --- promo route behaviour ----------------------------------------------------

def _rows(n):
    return [_cl(100 - i, float(100 - i)) for i in range(n)]


def test_route_returns_promo_for_eligible_launcher(client_and_session):
    client, session, _ = client_and_session
    session.rows = _rows(16)
    body = client.get("/api/v1/launcher/feed",
                      params={"limit": 15, "session_age_days": 5}).json()
    assert [i["type"] for i in body["items"]][-1] == "promo"
    assert body["items"][-1]["cta"] == {
        "label": "Get the News app", "target": "news_app", "deeplink": None,
        "campaign": "continue", "content": "feed_end",
    }


def test_route_cached_page_stays_promo_free_and_shared(client_and_session):
    client, session, store = client_and_session
    session.rows = _rows(16)
    with_promo = client.get("/api/v1/launcher/feed", params={"limit": 15, "session_age_days": 5}).json()
    installed = client.get("/api/v1/launcher/feed",
                           params={"limit": 15, "session_age_days": 5, "news_installed": "true"}).json()
    anon = client.get("/api/v1/launcher/feed", params={"limit": 15}).json()
    assert any(i["type"] == "promo" for i in with_promo["items"])
    # News installed: no install promo, only the playable game tile.
    assert [i["type"] for i in installed["items"] if i["type"] != "story"] == ["game_tile"]
    assert all(i["type"] == "story" for i in anon["items"])
    assert len(session.statements) == 1  # one DB query served all three
    assert all('"type":"promo"' not in v for v in store.values())


def test_route_dismissed_promo_not_served(client_and_session):
    client, session, _ = client_and_session
    session.rows = _rows(16)
    body = client.get("/api/v1/launcher/feed", params={
        "limit": 15, "session_age_days": 5,
        "dismissed": f"x, {CONTINUATION_PROMO_ID},{game_tile(date(2026, 10, 27))['id']},"}).json()
    assert all(i["type"] == "story" for i in body["items"])


def test_route_no_promo_on_cursor_page(client_and_session):
    client, session, _ = client_and_session
    session.rows = _rows(16)
    body = client.get("/api/v1/launcher/feed", params={
        "limit": 15, "session_age_days": 5, "cursor": encode_cursor(50.0, 50)}).json()
    assert all(i["type"] == "story" for i in body["items"])


def test_route_rejects_negative_session_age(client_and_session):
    client, _, _ = client_and_session
    assert client.get("/api/v1/launcher/feed", params={"session_age_days": -1}).status_code == 422


def test_route_includes_teaser_on_story(client_and_session):
    client, session, _ = client_and_session
    c = tcluster()
    c.headline_score = 5.0
    session.rows = [c]
    item = client.get("/api/v1/launcher/feed").json()["items"][0]
    assert item["teaser"]["source_count"] == 5 and item["teaser"]["angle"]["outlet"] == "The Hindu"


# --- daily game tile ----------------------------------------------------------

DAY = date(2026, 10, 27)


def test_game_rotation_is_deterministic_and_covers_every_game_weekly():
    assert game_for_date(DAY) == game_for_date(DAY)
    week = {game_for_date(DAY + timedelta(days=i)) for i in range(7)}
    assert week == set(GAME_TYPES)


def test_game_tile_shape_and_links():
    t = game_tile(DAY)
    g = game_for_date(DAY)
    assert t["type"] == "game_tile" and t["id"] == f"game_{g}_2026-10-27" and t["game"] == g
    assert t["cta"]["deeplink"] == f"https://openindiannews.com/games/{g}/daily"
    assert t["cta"]["campaign"] == "game" and t["cta"]["content"] == f"game_{g}"
    from app.launcher_schemas import LauncherGameTileItem
    LauncherGameTileItem(**t)


def test_game_types_match_what_the_news_app_can_open():
    assert GAME_TYPES == ("crossword", "wordle", "sudoku", "word_search", "spelling_bee", "word_ladder", "quiz")


def games_apply(n=15, **kw):
    base = dict(is_first_page=True, news_installed=False, session_age_days=5, today=DAY)
    base.update(kw)
    return apply_promos(stories(n), **base)


def test_game_tile_sits_in_fourth_slot_before_continuation_card():
    out = games_apply()
    assert [i["type"] for i in out][3] == "game_tile" and out[-1]["id"] == CONTINUATION_PROMO_ID
    assert sum(1 for i in out if i["type"] == "story") == 15


def test_game_tile_served_when_news_installed_even_for_a_new_launcher():
    for age in (0, 1, 30):
        out = games_apply(news_installed=True, session_age_days=age)
        assert [i["type"] for i in out if i["type"] != "story"] == ["game_tile"]


def test_game_tile_held_back_for_new_launcher_without_news():
    for age in (0, 1):
        assert all(i["type"] == "story" for i in games_apply(session_age_days=age))


def test_unknown_session_age_gets_nothing_even_with_news_installed():
    for installed in (False, True):
        assert all(i["type"] == "story" for i in games_apply(news_installed=installed, session_age_days=None))


def test_game_tile_only_on_first_page_and_never_dismissed_one():
    assert all(i["type"] == "story" for i in games_apply(is_first_page=False))
    out = games_apply(dismissed=[game_tile(DAY)["id"]])
    assert "game_tile" not in [i["type"] for i in out]


def test_yesterdays_dismissal_does_not_hide_todays_tile():
    yesterday = game_tile(date(2026, 10, 26))["id"]
    assert "game_tile" in [i["type"] for i in games_apply(dismissed=[yesterday])]


def test_game_tile_without_today_is_not_added():
    assert "game_tile" not in [i["type"] for i in games_apply(today=None)]


def test_route_serves_game_tile_in_fourth_position(client_and_session):
    client, session, _ = client_and_session
    session.rows = _rows(16)
    body = client.get("/api/v1/launcher/feed", params={"limit": 15, "session_age_days": 5}).json()
    assert [i["type"] for i in body["items"]][3] == "game_tile"
    assert body["items"][3]["cta"]["deeplink"].startswith("https://openindiannews.com/games/")
