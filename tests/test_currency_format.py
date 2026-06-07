from unittest.mock import patch

import pytest

from app.services import currency_format as cf
from app.services.settings_service import set_setting


@pytest.fixture(autouse=True)
def _reset_currency_cache():
    cf.invalidate_cache()
    yield
    cf.invalidate_cache()


def test_get_current_currency_defaults_to_vnd(db_session):
    assert cf.get_current_currency(db_session) == "VND"


def test_get_current_currency_caches_value(db_session):
    set_setting(db_session, "display_currency", "USD")
    assert cf.get_current_currency(db_session) == "USD"
    # Cached value is reused across calls without re-querying.
    assert cf.get_current_currency(db_session) == "USD"
    assert cf._cache["value"] == "USD"


def test_get_current_currency_opens_own_session_when_none_passed(db_session):
    set_setting(db_session, "display_currency", "EUR")
    cf.invalidate_cache()
    with patch("app.services.currency_format.SessionLocal", return_value=db_session):
        assert cf.get_current_currency() == "EUR"


def test_invalidate_cache_clears_cached_value(db_session):
    cf.get_current_currency(db_session)
    assert cf._cache["value"] is not None
    cf.invalidate_cache()
    assert cf._cache["value"] is None


def test_abbreviate_billions_millions_and_plain():
    assert cf.abbreviate(1_500_000_000) == "1.50B"
    assert cf.abbreviate(2_500_000) == "2M"
    assert cf.abbreviate(1234) == "1,234"
    assert cf.abbreviate(None) == "0"


def test_format_amount_prefix_and_suffix():
    assert cf.format_amount(1234567, "VND") == "1,234,567 ₫"
    assert cf.format_amount(1234567, "USD") == "$1,234,567"
    assert cf.format_amount(1234567, "EUR") == "1,234,567 €"


def test_format_amount_signed():
    assert cf.format_amount(-5000, "VND", signed=True) == "-5,000 ₫"
    assert cf.format_amount(5000, "VND", signed=True) == "+5,000 ₫"


def test_format_amount_unknown_currency_falls_back_to_default():
    assert cf.format_amount(1000, "XYZ") == "1,000 ₫"


def test_format_amount_uses_current_setting_when_currency_not_passed(db_session):
    set_setting(db_session, "display_currency", "USD")
    cf.invalidate_cache()
    with patch("app.services.currency_format.SessionLocal", return_value=db_session):
        assert cf.format_amount(1000) == "$1,000"


def test_format_amount_abbrev_billions_millions_plain():
    assert cf.format_amount_abbrev(1_500_000_000, "VND") == "1.50B ₫"
    assert cf.format_amount_abbrev(2_500_000, "VND") == "2M ₫"
    assert cf.format_amount_abbrev(1234, "VND") == "1,234 ₫"


def test_format_amount_abbrev_prefix_currency():
    assert cf.format_amount_abbrev(1_500_000_000, "USD") == "$1.50B"


def test_format_amount_abbrev_signed():
    assert cf.format_amount_abbrev(-1_200_000_000, "VND", signed=True) == "-1.20B ₫"
    assert cf.format_amount_abbrev(1_200_000_000, "VND", signed=True) == "+1.20B ₫"


def test_jinja_currency_and_abbrev_filters(db_session):
    set_setting(db_session, "display_currency", "VND")
    cf.invalidate_cache()
    assert cf.jinja_currency(1000) == "1,000 ₫"
    assert cf.jinja_currency(-1000, signed=True) == "-1,000 ₫"
    assert cf.jinja_currency_abbrev(1_500_000_000) == "1.50B ₫"


def test_jinja_currency_symbol(db_session):
    set_setting(db_session, "display_currency", "USD")
    cf.invalidate_cache()
    with patch("app.services.currency_format.SessionLocal", return_value=db_session):
        assert cf.jinja_currency_symbol() == "$"


def test_register_adds_filters_and_globals():
    class FakeEnv:
        def __init__(self):
            self.filters = {}
            self.globals = {}

    env = FakeEnv()
    cf.register(env)
    assert env.filters["currency"] is cf.jinja_currency
    assert env.filters["currency_abbrev"] is cf.jinja_currency_abbrev
    assert env.filters["abbrev"] is cf.abbreviate
    assert env.globals["currency_symbol"] is cf.jinja_currency_symbol


def test_inject_currency_returns_display_currency(db_session):
    set_setting(db_session, "display_currency", "EUR")
    cf.invalidate_cache()
    with patch("app.services.currency_format.SessionLocal", return_value=db_session):
        assert cf.inject_currency(request=None) == {"display_currency": "EUR"}
