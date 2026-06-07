"""Tests for the dashboard layout preset feature (simple/standard/full)."""

import pytest

from app.services.dashboard_layout import (
    DEFAULT_PRESET,
    PRESETS,
    get_dashboard_preset,
    get_visible_sections,
    set_dashboard_preset,
)


def test_default_preset_is_full(db_session):
    assert get_dashboard_preset(db_session) == DEFAULT_PRESET == "full"
    assert get_visible_sections(db_session) == PRESETS["full"]


def test_set_dashboard_preset_persists(db_session):
    set_dashboard_preset(db_session, "simple")
    assert get_dashboard_preset(db_session) == "simple"
    assert get_visible_sections(db_session) == frozenset()


def test_set_dashboard_preset_rejects_unknown(db_session):
    with pytest.raises(ValueError):
        set_dashboard_preset(db_session, "ultra")


def test_unknown_stored_value_falls_back_to_default(db_session):
    from app.services.settings_service import set_setting

    set_setting(db_session, "dashboard_layout", "garbage")
    assert get_dashboard_preset(db_session) == DEFAULT_PRESET


@pytest.mark.parametrize(
    "preset, hidden_text",
    [
        ("simple", "Wealth Building Analysis"),
        ("simple", "Cash Flow — Last 6 Months"),
        ("standard", "Wealth Building Analysis"),
    ],
)
def test_dashboard_page_hides_sections_per_preset(client, db_session, preset, hidden_text):
    set_dashboard_preset(db_session, preset)
    r = client.get("/")
    assert r.status_code == 200
    assert hidden_text not in r.text


def test_dashboard_page_full_preset_shows_everything(client, db_session):
    set_dashboard_preset(db_session, "full")
    r = client.get("/")
    assert r.status_code == 200
    assert "Wealth Building Analysis" in r.text
    assert "One-Income Stress Test" in r.text
    assert "Cash Flow — Last 6 Months" in r.text
    assert "Budget Snapshot" in r.text
    assert "Active Projects" in r.text
    assert "Savings Goals" in r.text


def test_dashboard_page_core_kpis_always_visible(client, db_session):
    for preset in PRESETS:
        set_dashboard_preset(db_session, preset)
        r = client.get("/")
        assert "Net Worth" in r.text
        assert "Net Cash" in r.text
        assert "Recent Transactions" in r.text


def test_kpi_extra_cards_hidden_in_simple_preset(client, db_session):
    """Card markup (not the always-present info-popover JS data) should be absent."""
    set_dashboard_preset(db_session, "simple")
    r = client.get("/")
    assert "showKpiInfo('bds-rate'" not in r.text
    assert "showKpiInfo('emergency-fund'" not in r.text
    assert "showKpiInfo('fi-progress'" not in r.text


def test_kpi_extra_cards_shown_in_standard_preset(client, db_session):
    set_dashboard_preset(db_session, "standard")
    r = client.get("/")
    assert "showKpiInfo('bds-rate'" in r.text
    assert "showKpiInfo('emergency-fund'" in r.text
    assert "showKpiInfo('fi-progress'" in r.text
