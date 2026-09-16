from app.admin_session import admin_public_path, admin_url, _publicize_admin_html


def test_admin_public_path_strips_internal_prefix():
    assert admin_public_path("/admin") == "/"
    assert admin_public_path("/admin/polls") == "/polls"
    assert admin_public_path("/admin/users?q=alice") == "/users?q=alice"
    assert admin_public_path("/administrator") == "/administrator"


def test_admin_url_uses_canonical_subdomain():
    assert admin_url("/admin/polls") == "https://admin.openindiannews.com/polls"


def test_admin_html_uses_public_paths():
    html = (
        "<a href='/admin/polls'>Polls</a>"
        "<form action='/admin/quiz/update'>"
        "<a href='/api/v1/clusters/1'>API</a>"
    )
    rendered = _publicize_admin_html(html)
    assert "href='/polls'" in rendered
    assert "action='/quiz/update'" in rendered
    assert "/api/v1/clusters/1" in rendered
