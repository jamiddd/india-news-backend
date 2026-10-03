from app.admin_session import admin_public_path, admin_url


def test_admin_public_path_strips_internal_prefix():
    assert admin_public_path("/admin") == "/"
    assert admin_public_path("/admin/polls") == "/polls"
    assert admin_public_path("/admin/users?q=alice") == "/users?q=alice"
    assert admin_public_path("/administrator") == "/administrator"


def test_admin_url_uses_canonical_subdomain():
    assert admin_url("/admin/polls") == "https://admin.openindiannews.com/polls"
