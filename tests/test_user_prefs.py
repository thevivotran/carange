"""Per-profile nav/dashboard preference behavior: seeding, preset matching,
independence between profiles, and template rendering."""

from app.models.database import User
from app.services.dashboard_layout import (
    NAV_CORE,
    NAV_PRESETS,
    get_user_nav_items,
    match_nav_preset,
    seed_user_prefs_from_globals,
    set_user_nav_items,
)


def test_seed_user_prefs_respects_household_presets(db_session, profile_row):
    from app.services.dashboard_layout import get_user_sections
    from app.services.settings_service import set_setting

    set_setting(db_session, "nav_layout", "standard")
    set_setting(db_session, "dashboard_layout", "simple")
    seed_user_prefs_from_globals(db_session, profile_row.id)

    assert get_user_nav_items(db_session, profile_row.id) == NAV_CORE | NAV_PRESETS["standard"]
    assert get_user_sections(db_session, profile_row.id) == frozenset()


def test_nav_items_are_independent_per_profile(db_session, profile_row):
    other = User(id=2, name="Other", color="#059669")
    db_session.add(other)
    db_session.commit()

    set_user_nav_items(db_session, profile_row.id, ["pulse"])
    set_user_nav_items(db_session, other.id, ["assets", "notes"])

    assert get_user_nav_items(db_session, profile_row.id) == NAV_CORE | {"pulse"}
    assert get_user_nav_items(db_session, other.id) == NAV_CORE | {"assets", "notes"}


def test_nav_items_fall_back_to_household_default_when_unset(db_session, profile_row):
    from app.services.settings_service import set_setting

    set_setting(db_session, "nav_layout", "simple")
    assert get_user_nav_items(db_session, profile_row.id) == NAV_CORE


def test_match_nav_preset():
    assert match_nav_preset(NAV_CORE) == "simple"
    assert match_nav_preset(NAV_CORE | NAV_PRESETS["standard"]) == "standard"
    assert match_nav_preset(NAV_CORE | NAV_PRESETS["full"]) == "full"
    assert match_nav_preset(NAV_CORE | {"pulse"}) is None


def test_nav_toggles_control_sidebar_rendering(client, set_profile_ctx):
    set_profile_ctx(nav_items=NAV_CORE | {"pulse"})
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/pulse"' in r.text
    assert 'href="/assets"' not in r.text
    assert 'href="/notes"' not in r.text


def test_profile_chip_rendered_in_sidebar(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/profiles"' in r.text  # switch-profile link
    assert "Test" in r.text  # stub profile name


def test_settings_page_marks_custom_when_no_preset_matches(client, db_session, profile_row):
    from app.services.dashboard_layout import set_user_sections

    set_user_sections(db_session, profile_row.id, ["cash_flow"])
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Custom" in r.text
