"""Tests for the household profile picker: cookie selection, create/delete,
and the ProfileMiddleware gating of non-public routes."""

from app.models.database import User, UserSetting


def _create_user(db, name="Vi", color="#2563EB"):
    user = User(name=name, color=color)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ── Middleware gating ─────────────────────────────────────────────────────────


def test_browser_get_without_profile_redirects_to_picker(profile_client):
    r = profile_client.get("/transactions", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/profiles?next=/transactions"


def test_htmx_request_without_profile_gets_hx_redirect(profile_client):
    r = profile_client.get("/", headers={"HX-Request": "true"})
    assert r.status_code == 401
    assert r.headers["HX-Redirect"] == "/profiles"


def test_non_html_request_without_profile_gets_401_json(profile_client):
    r = profile_client.get("/transactions")
    assert r.status_code == 401
    assert r.json()["detail"] == "No profile selected"


def test_public_paths_reachable_without_profile(profile_client):
    assert profile_client.get("/health").status_code == 200
    r = profile_client.get("/profiles")
    assert r.status_code == 200
    assert "Create your first profile" in r.text


def test_stale_cookie_redirects_to_picker(profile_client):
    profile_client.cookies.set("carange_profile", "999")
    r = profile_client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/profiles")


# ── Create / select ───────────────────────────────────────────────────────────


def test_create_first_profile_sets_cookie_and_seeds_prefs(profile_client, db_session):
    from app.services.dashboard_layout import (
        NAV_CORE,
        NAV_PRESETS,
        PRESETS,
        get_user_nav_items,
        get_user_sections,
    )

    r = profile_client.post("/profiles/create", data={"name": "Vi", "color": "#2563EB"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "carange_profile=" in r.headers.get("set-cookie", "")

    user = db_session.query(User).filter(User.name == "Vi").first()
    assert user is not None
    # Seeded from household defaults (full preset out of the box)
    assert get_user_sections(db_session, user.id) == PRESETS["full"]
    assert get_user_nav_items(db_session, user.id) == NAV_CORE | NAV_PRESETS["full"]

    # Cookie persisted by the client — pages now resolve
    page = profile_client.get("/")
    assert page.status_code == 200
    assert "Vi" in page.text


def test_select_profile_resolves_pages_and_touches_last_seen(profile_client, db_session):
    user = _create_user(db_session)
    assert user.last_seen_at is None

    r = profile_client.post(
        "/profiles/select", data={"user_id": str(user.id), "next": "/budget"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/budget"

    db_session.refresh(user)
    assert user.last_seen_at is not None

    page = profile_client.get("/")
    assert page.status_code == 200


def test_select_unknown_profile_shows_error(profile_client, db_session):
    _create_user(db_session)
    r = profile_client.post("/profiles/select", data={"user_id": "999"})
    assert r.status_code == 200
    assert "no longer exists" in r.text


def test_duplicate_name_shows_error(profile_client, db_session):
    _create_user(db_session, name="Vi")
    r = profile_client.post("/profiles/create", data={"name": "Vi"})
    assert r.status_code == 200
    assert "already exists" in r.text
    assert db_session.query(User).count() == 1


def test_create_with_empty_name_shows_error(profile_client, db_session):
    r = profile_client.post("/profiles/create", data={"name": "   "})
    assert r.status_code == 200
    assert "Please enter a name" in r.text
    assert db_session.query(User).count() == 0


def test_create_with_unknown_color_falls_back_to_default(profile_client, db_session):
    from app.services.profiles import DEFAULT_PROFILE_COLOR

    r = profile_client.post("/profiles/create", data={"name": "Vi", "color": "#BADBAD"}, follow_redirects=False)
    assert r.status_code == 303
    user = db_session.query(User).filter(User.name == "Vi").first()
    assert user.color == DEFAULT_PROFILE_COLOR


def test_next_path_is_validated_against_open_redirects(profile_client, db_session):
    user = _create_user(db_session)
    r = profile_client.post(
        "/profiles/select",
        data={"user_id": str(user.id), "next": "//evil.example.com"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"


# ── Delete ────────────────────────────────────────────────────────────────────


def test_cannot_delete_last_profile(profile_client, db_session):
    user = _create_user(db_session)
    r = profile_client.post(f"/profiles/{user.id}/delete")
    assert r.status_code == 200
    assert "Cannot delete the last profile" in r.text
    assert db_session.query(User).count() == 1


def test_delete_unknown_profile_shows_error(profile_client, db_session):
    _create_user(db_session)
    r = profile_client.post("/profiles/999/delete")
    assert r.status_code == 200
    assert "no longer exists" in r.text


def test_delete_current_profile_clears_cookie(profile_client, db_session):
    _create_user(db_session, name="Vi")
    other = _create_user(db_session, name="Wife", color="#059669")

    profile_client.post("/profiles/select", data={"user_id": str(other.id)}, follow_redirects=False)
    r = profile_client.post(f"/profiles/{other.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    # Cookie cleared → back to the picker on the next page visit
    page = profile_client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["location"].startswith("/profiles")


def test_delete_profile_removes_its_settings(profile_client, db_session):
    from app.services.dashboard_layout import set_user_sections

    _create_user(db_session, name="Vi")
    other = _create_user(db_session, name="Wife", color="#059669")
    set_user_sections(db_session, other.id, ["cash_flow"])

    r = profile_client.post(f"/profiles/{other.id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert db_session.query(User).filter(User.id == other.id).count() == 0
    assert db_session.query(UserSetting).filter(UserSetting.user_id == other.id).count() == 0
