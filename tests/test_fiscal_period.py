"""Unit tests for app.services.fiscal_period.

The day=1 cases assert byte-for-byte equivalence with calendar months so
the configurable start-day is a strict superset of current behavior.
"""

from datetime import date

from app.services import fiscal_period as fp
from app.services.settings_service import set_setting


# ── fiscal_window_ym: day=1 backward-compat ──────────────────────────────────


def test_fiscal_window_ym_day1_february_non_leap():
    assert fp.fiscal_window_ym(2026, 2, 1) == (date(2026, 2, 1), date(2026, 2, 28))


def test_fiscal_window_ym_day1_february_leap():
    assert fp.fiscal_window_ym(2024, 2, 1) == (date(2024, 2, 1), date(2024, 2, 29))


def test_fiscal_window_ym_day1_december():
    assert fp.fiscal_window_ym(2026, 12, 1) == (date(2026, 12, 1), date(2026, 12, 31))


# ── fiscal_window: day=19 ────────────────────────────────────────────────────


def test_fiscal_window_day19_june():
    assert fp.fiscal_window("2026-06", 19) == (date(2026, 6, 19), date(2026, 7, 18))


def test_fiscal_window_day19_december_year_rollover():
    assert fp.fiscal_window("2026-12", 19) == (date(2026, 12, 19), date(2027, 1, 18))


# ── current_period_ym ────────────────────────────────────────────────────────


def test_current_period_ym_day19_before_start():
    # June 10 is before the 19th → belongs to May's period
    assert fp.current_period_ym(date(2026, 6, 10), 19) == (2026, 5)


def test_current_period_ym_day19_on_start():
    assert fp.current_period_ym(date(2026, 6, 19), 19) == (2026, 6)


def test_current_period_ym_day19_end_of_month():
    assert fp.current_period_ym(date(2026, 6, 30), 19) == (2026, 6)


def test_current_period_ym_day1_always_same_month():
    # With day=1 every date belongs to its own calendar month
    for d in [date(2026, 1, 1), date(2026, 6, 15), date(2026, 12, 31)]:
        assert fp.current_period_ym(d, 1) == (d.year, d.month)


def test_current_period_ym_january_underflow():
    # January 5 with day=19 → belongs to December of prior year
    assert fp.current_period_ym(date(2026, 1, 5), 19) == (2025, 12)


# ── shift_period_label / prev_period_label ───────────────────────────────────


def test_shift_period_label_back_across_year():
    assert fp.shift_period_label("2026-01", -1) == "2025-12"


def test_shift_period_label_forward_across_year():
    assert fp.shift_period_label("2026-12", 1) == "2027-01"


def test_prev_period_label():
    assert fp.prev_period_label("2026-06") == "2026-05"
    assert fp.prev_period_label("2026-01") == "2025-12"


# ── days_in_period / day_index_in_period ─────────────────────────────────────


def test_days_in_period_day19_june():
    # June 19 .. July 18 inclusive → 12 days in June + 18 in July = 30
    assert fp.days_in_period("2026-06", 19) == 30


def test_days_in_period_day1_february_leap():
    assert fp.days_in_period("2024-02", 1) == 29


def test_day_index_in_period_second_day():
    # June 20 is the second day of the "2026-06" period (starts June 19)
    assert fp.day_index_in_period(date(2026, 6, 20), 19) == 2


def test_day_index_in_period_first_day():
    assert fp.day_index_in_period(date(2026, 6, 19), 19) == 1


def test_day_index_in_period_spanning_months():
    # July 5 with day=19 → still in "2026-06" period (June 19 .. July 18)
    # Position = (July 5 - June 19).days + 1 = 16 + 1 = 17
    assert fp.day_index_in_period(date(2026, 7, 5), 19) == 17


# ── get_month_start_day ──────────────────────────────────────────────────────


def test_get_month_start_day_default_when_unset(db_session):
    assert fp.get_month_start_day(db_session) == 1


def test_get_month_start_day_stores_and_reads_19(db_session):
    set_setting(db_session, fp.SETTING_KEY, "19")
    assert fp.get_month_start_day(db_session) == 19


def test_get_month_start_day_clamps_zero_to_min(db_session):
    set_setting(db_session, fp.SETTING_KEY, "0")
    assert fp.get_month_start_day(db_session) == 1


def test_get_month_start_day_clamps_forty_to_max(db_session):
    set_setting(db_session, fp.SETTING_KEY, "40")
    assert fp.get_month_start_day(db_session) == 31


def test_get_month_start_day_non_numeric_falls_back(db_session):
    set_setting(db_session, fp.SETTING_KEY, "x")
    assert fp.get_month_start_day(db_session) == 1


def test_fiscal_window_day31_clamps_in_short_months():
    assert fp.fiscal_window_ym(2026, 2, 31) == (date(2026, 2, 28), date(2026, 3, 30))


def test_get_month_start_day_clamps_to_31_not_28(db_session):
    set_setting(db_session, fp.SETTING_KEY, "30")
    assert fp.get_month_start_day(db_session) == 30


def test_suggest_salary_day_none_without_templates(db_session):
    assert fp.suggest_salary_day(db_session) is None


def test_suggest_salary_day_from_monthly_income_template(db_session, income_cat):
    from app.models.database import TransactionTemplate, TransactionType

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

    assert fp.suggest_salary_day(db_session) == 19
