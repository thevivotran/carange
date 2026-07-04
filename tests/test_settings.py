"""Tests for the Settings pay-cycle (month_start_day) handler."""

from app.services.settings_service import get_setting, set_setting


class TestSavePayCycle:
    def test_saves_valid_day(self, client, db_session):
        r = client.post("/settings/pay-cycle", data={"month_start_day": "19"})
        assert r.status_code == 200
        assert get_setting(db_session, "month_start_day") == "19"

    def test_clamps_below_min(self, client, db_session):
        # 0 is invalid; the handler must reject it with 400 (not silently clamp).
        r = client.post("/settings/pay-cycle", data={"month_start_day": "0"})
        assert r.status_code == 400

    def test_clamps_above_max(self, client, db_session):
        # 40 is invalid; the handler must reject it with 400 (not silently clamp).
        r = client.post("/settings/pay-cycle", data={"month_start_day": "40"})
        assert r.status_code == 400

    def test_pay_cycle_accepts_28(self, client, db_session):
        # 28 is the new max (every month has at least 28 days).
        from app.services.fiscal_period import get_month_start_day

        r = client.post("/settings/pay-cycle", data={"month_start_day": "28"})
        assert r.status_code == 200
        assert get_month_start_day(db_session) == 28

    def test_pay_cycle_rejects_29(self, client, db_session):
        # 29 is invalid (Feb has 28-29 in non-leap years; 28 is the safe max).
        r = client.post("/settings/pay-cycle", data={"month_start_day": "29"})
        assert r.status_code == 400

    def test_defaults_on_non_numeric(self, client, db_session):
        # Non-numeric input is invalid; the handler must reject it with 400
        # (the previous silent fallback to "1" hid user typos).
        r = client.post("/settings/pay-cycle", data={"month_start_day": "x"})
        assert r.status_code == 400

    def test_settings_page_renders_pay_cycle_card(self, client, db_session):
        r = client.get("/settings")
        assert r.status_code == 200
        assert b"Pay cycle" in r.content
        assert b"month_start_day" in r.content

    def test_pay_cycle_unchanged_shows_plain_saved(self, client, db_session):
        client.post("/settings/pay-cycle", data={"month_start_day": "1"})
        r = client.post("/settings/pay-cycle", data={"month_start_day": "1"})
        assert r.status_code == 200
        assert "Saved" in r.text
        assert "periods now run" not in r.text

    def test_pay_cycle_change_shows_explanation(self, client, db_session):
        r = client.post("/settings/pay-cycle", data={"month_start_day": "19"})
        assert r.status_code == 200
        assert "Saved" in r.text
        assert "periods now run" in r.text
        assert "19th" in r.text
        assert "re-grouped" in r.text


class TestSalaryDaySuggestion:
    def test_settings_page_shows_salary_suggestion(self, client, db_session, income_cat):
        from app.models.database import TransactionTemplate, TransactionType
        from datetime import date

        tmpl = TransactionTemplate(
            name="Salary",
            amount=20_000_000,
            type=TransactionType.INCOME,
            category_id=income_cat.id,
            is_active=True,
            cadence="monthly",
            next_run_at=date(2026, 7, 19),
        )
        db_session.add(tmpl)
        db_session.commit()

        r = client.get("/settings")
        assert r.status_code == 200
        assert "Use 19th" in r.text

    def test_settings_page_hides_suggestion_when_matching(self, client, db_session, income_cat):
        from app.models.database import TransactionTemplate, TransactionType
        from app.services.fiscal_period import SETTING_KEY
        from datetime import date

        set_setting(db_session, SETTING_KEY, "19")

        tmpl = TransactionTemplate(
            name="Salary",
            amount=20_000_000,
            type=TransactionType.INCOME,
            category_id=income_cat.id,
            is_active=True,
            cadence="monthly",
            next_run_at=date(2026, 7, 19),
        )
        db_session.add(tmpl)
        db_session.commit()

        r = client.get("/settings")
        assert r.status_code == 200
        assert "Use 19th" not in r.text


class TestSaveForecastBuffer:
    def test_saves_valid_value(self, client, db_session):
        r = client.post("/settings/forecast-buffer", data={"forecast_buffer": "500000"})
        assert r.status_code == 200
        assert get_setting(db_session, "forecast_buffer") == "500000"

    def test_rejects_negative_value(self, client, db_session):
        set_setting(db_session, "forecast_buffer", "100")
        r = client.post("/settings/forecast-buffer", data={"forecast_buffer": "-1"})
        assert r.status_code == 400
        assert get_setting(db_session, "forecast_buffer") == "100"

    def test_rejects_non_numeric_value(self, client, db_session):
        set_setting(db_session, "forecast_buffer", "100")
        r = client.post("/settings/forecast-buffer", data={"forecast_buffer": "abc"})
        assert r.status_code == 400
        assert get_setting(db_session, "forecast_buffer") == "100"

    def test_settings_page_renders_buffer_card(self, client, db_session):
        r = client.get("/settings")
        assert r.status_code == 200
        assert b"Cash buffer" in r.content
        assert b"forecast_buffer" in r.content
