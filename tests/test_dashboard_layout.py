"""Tests for per-profile dashboard layout (presets + per-section toggles)."""

import pytest

from app.services.dashboard_layout import (
    DEFAULT_PRESET,
    NAV_ITEM_DESCRIPTIONS,
    NAV_ITEM_LABELS,
    PRESETS,
    SECTION_DESCRIPTIONS,
    SECTION_LABELS,
    TOGGLEABLE_NAV_ITEMS,
    TOGGLEABLE_SECTIONS,
    apply_dashboard_preset,
    get_dashboard_preset,
    get_user_sections,
    get_visible_sections,
    match_dashboard_preset,
    set_user_sections,
)


def test_forecast_nav_item_registered():
    assert "forecast" in TOGGLEABLE_NAV_ITEMS
    assert "forecast" in NAV_ITEM_LABELS
    assert "forecast" in NAV_ITEM_DESCRIPTIONS


def test_cash_outlook_section_registered():
    assert "cash_outlook" in TOGGLEABLE_SECTIONS
    assert "cash_outlook" in SECTION_LABELS
    assert "cash_outlook" in SECTION_DESCRIPTIONS


def test_default_household_preset_is_full(db_session):
    assert get_dashboard_preset(db_session) == DEFAULT_PRESET == "full"
    assert get_visible_sections(db_session) == PRESETS["full"]


def test_profile_without_prefs_falls_back_to_household_default(db_session, profile_row):
    from app.services.settings_service import set_setting

    set_setting(db_session, "dashboard_layout", "simple")
    assert get_user_sections(db_session, profile_row.id) == frozenset()


def test_apply_dashboard_preset_persists(db_session, profile_row):
    apply_dashboard_preset(db_session, profile_row.id, "simple")
    assert get_user_sections(db_session, profile_row.id) == frozenset()
    apply_dashboard_preset(db_session, profile_row.id, "full")
    assert get_user_sections(db_session, profile_row.id) == PRESETS["full"]


def test_apply_dashboard_preset_rejects_unknown(db_session, profile_row):
    with pytest.raises(ValueError):
        apply_dashboard_preset(db_session, profile_row.id, "ultra")


def test_set_user_sections_filters_unknown_keys(db_session, profile_row):
    set_user_sections(db_session, profile_row.id, ["cash_flow", "bogus"])
    assert get_user_sections(db_session, profile_row.id) == frozenset({"cash_flow"})


def test_sections_are_independent_per_profile(db_session, profile_row):
    from app.models.database import User

    other = User(id=2, name="Other", color="#059669")
    db_session.add(other)
    db_session.commit()

    set_user_sections(db_session, profile_row.id, ["cash_flow"])
    set_user_sections(db_session, other.id, ["stress_test", "wealth_building"])

    assert get_user_sections(db_session, profile_row.id) == frozenset({"cash_flow"})
    assert get_user_sections(db_session, other.id) == frozenset({"stress_test", "wealth_building"})


def test_unknown_stored_household_value_falls_back_to_default(db_session):
    from app.services.settings_service import set_setting

    set_setting(db_session, "dashboard_layout", "garbage")
    assert get_dashboard_preset(db_session) == DEFAULT_PRESET


def test_match_dashboard_preset():
    assert match_dashboard_preset(PRESETS["simple"]) == "simple"
    assert match_dashboard_preset(PRESETS["standard"]) == "standard"
    assert match_dashboard_preset(PRESETS["full"]) == "full"
    assert match_dashboard_preset(frozenset({"cash_flow"})) is None


@pytest.mark.parametrize(
    "preset, hidden_text",
    [
        ("simple", "Wealth Building Analysis"),
        ("simple", "Cash Flow — Last 6 Months"),
        ("standard", "Wealth Building Analysis"),
    ],
)
def test_dashboard_page_hides_sections_per_preset(client, set_profile_ctx, preset, hidden_text):
    set_profile_ctx(sections=PRESETS[preset])
    r = client.get("/")
    assert r.status_code == 200
    assert hidden_text not in r.text


def test_dashboard_page_full_preset_shows_everything(client, set_profile_ctx):
    set_profile_ctx(sections=PRESETS["full"])
    r = client.get("/")
    assert r.status_code == 200
    assert "Wealth Building Analysis" in r.text
    assert "One-Income Stress Test" in r.text
    assert "Cash Flow — Last 6 Months" in r.text
    assert "Budget Snapshot" in r.text
    assert "Active Projects" in r.text
    assert "Savings Goals" in r.text


def test_dashboard_page_core_kpis_always_visible(client, set_profile_ctx):
    for preset in PRESETS:
        set_profile_ctx(sections=PRESETS[preset])
        r = client.get("/")
        assert "Net Worth" in r.text
        assert "Net Cash" in r.text
        assert "Recent Transactions" in r.text


def test_kpi_extra_cards_hidden_in_simple_preset(client, set_profile_ctx):
    """Card markup (not the always-present info-popover JS data) should be absent."""
    set_profile_ctx(sections=PRESETS["simple"])
    r = client.get("/")
    assert "showKpiInfo('bds-rate'" not in r.text
    assert "showKpiInfo('emergency-fund'" not in r.text
    assert "showKpiInfo('fi-progress'" not in r.text


def test_kpi_extra_cards_shown_in_standard_preset(client, set_profile_ctx):
    set_profile_ctx(sections=PRESETS["standard"])
    r = client.get("/")
    assert "showKpiInfo('bds-rate'" in r.text
    assert "showKpiInfo('emergency-fund'" in r.text
    assert "showKpiInfo('fi-progress'" in r.text
