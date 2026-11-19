from datetime import date, datetime

from app.services.date_format import app_date


def test_default_short_and_long_styles():
    d = date(2026, 11, 19)
    assert app_date(d) == "19 Nov 2026"
    assert app_date(d, "short") == "19 Nov"
    assert app_date(d, "long") == "Thu, 19 Nov 2026"


def test_single_digit_day_has_no_leading_zero():
    assert app_date(date(2026, 3, 4)) == "4 Mar 2026"


def test_accepts_datetime_and_iso_string():
    assert app_date(datetime(2026, 1, 2, 23, 59)) == "2 Jan 2026"
    assert app_date("2026-12-31") == "31 Dec 2026"
    assert app_date("2026-12-31T10:00:00") == "31 Dec 2026"


def test_empty_and_unparseable_values():
    assert app_date(None) == ""
    assert app_date("") == ""
    assert app_date("not a date") == "not a date"


def test_unknown_style_falls_back_to_default():
    assert app_date(date(2026, 5, 1), "weird") == "1 May 2026"


def test_dashboard_uses_app_date(client):
    assert f"as of {app_date(date.today())}" in client.get("/").text
