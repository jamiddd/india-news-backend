"""Daily-game App Links must not 404 in a browser.

https://openindiannews.com/games/{type}/daily is shared from the app and opens
in the app when it is installed. Without the app the browser fetches the page:
with the web app built it gets the web app's shell, and with the web app off
(static-pages-only mode) it is sent to the Play listing.
"""
import pytest
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main.limiter, "enabled", False)
    return TestClient(main.app)


@pytest.mark.parametrize("path", ["/games/wordle/daily", "/games/crossword/daily", "/games"])
def test_without_the_web_app_games_links_go_to_the_play_listing(client, monkeypatch, path):
    monkeypatch.setattr(main.web_spa, "render_shell", lambda **_kw: None)
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == main.PLAY_STORE_URL


def test_with_the_web_app_built_games_links_get_the_shell(client, monkeypatch):
    monkeypatch.setattr(
        main.web_spa, "render_shell",
        lambda **_kw: HTMLResponse("<html>shell</html>", headers={"Cache-Control": "no-cache"}),
    )
    r = client.get("/games/wordle/daily", follow_redirects=False)
    assert r.status_code == 200
    assert "shell" in r.text
