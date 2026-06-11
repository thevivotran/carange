"""Tests for the Settings pay-cycle (month_start_day) handler."""

from app.services.settings_service import get_setting


class TestSavePayCycle:
    def test_saves_valid_day(self, client, db_session):
        r = client.post("/settings/pay-cycle", data={"month_start_day": "19"})
        assert r.status_code == 200
        assert get_setting(db_session, "month_start_day") == "19"

    def test_clamps_below_min(self, client, db_session):
        r = client.post("/settings/pay-cycle", data={"month_start_day": "0"})
        assert r.status_code == 200
        assert get_setting(db_session, "month_start_day") == "1"

    def test_clamps_above_max(self, client, db_session):
        r = client.post("/settings/pay-cycle", data={"month_start_day": "40"})
        assert r.status_code == 200
        assert get_setting(db_session, "month_start_day") == "28"

    def test_defaults_on_non_numeric(self, client, db_session):
        r = client.post("/settings/pay-cycle", data={"month_start_day": "x"})
        assert r.status_code == 200
        assert get_setting(db_session, "month_start_day") == "1"

    def test_settings_page_renders_pay_cycle_card(self, client, db_session):
        r = client.get("/settings")
        assert r.status_code == 200
        assert b"Pay cycle" in r.content
        assert b"month_start_day" in r.content
