"""Display-currency formatting.

Purely cosmetic: swaps the symbol/placement shown for monetary amounts based
on the user's chosen "display_currency" setting. Stored amounts are never
converted — a value entered as VND is shown with a different symbol, not a
different magnitude.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import SessionLocal
from app.services.settings_service import get_setting

SETTING_KEY = "display_currency"
DEFAULT_CURRENCY = "VND"

CURRENCIES = {
    "VND": {"symbol": "₫", "position": "suffix", "label": "Vietnamese Đồng (₫)"},
    "USD": {"symbol": "$", "position": "prefix", "label": "US Dollar ($)"},
    "EUR": {"symbol": "€", "position": "suffix", "label": "Euro (€)"},
}

_cache: dict[str, Optional[str]] = {"value": None}


def get_current_currency(db: Optional[Session] = None) -> str:
    """Return the configured display currency code, cached in-process."""
    if _cache["value"] is None:
        if db is not None:
            _cache["value"] = get_setting(db, SETTING_KEY, DEFAULT_CURRENCY)
        else:
            session = SessionLocal()
            try:
                _cache["value"] = get_setting(session, SETTING_KEY, DEFAULT_CURRENCY)
            finally:
                session.close()
    return _cache["value"]


def invalidate_cache() -> None:
    _cache["value"] = None


def _config(currency: Optional[str]) -> dict:
    return CURRENCIES.get(currency or "", CURRENCIES[DEFAULT_CURRENCY])


def format_amount(amount, currency: Optional[str] = None, *, signed: bool = False) -> str:
    """Format `amount` with the chosen currency's symbol and placement.

    e.g. format_amount(1234567) -> "1,234,567 ₫"
         format_amount(1234567, "USD") -> "$1,234,567"
         format_amount(-500, signed=True) -> "-500 ₫"
    """
    cfg = _config(currency if currency is not None else get_current_currency())
    value = float(amount or 0)
    sign = ""
    if signed:
        sign = "+" if value >= 0 else "-"
        value = abs(value)
    number = f"{value:,.0f}"
    symbol = cfg["symbol"]
    body = f"{symbol}{number}" if cfg["position"] == "prefix" else f"{number} {symbol}"
    return f"{sign}{body}"


def abbreviate(amount) -> str:
    """Bare B/M abbreviation with no currency symbol, e.g. "1.23B" / "45M" / "1,234"."""
    value = float(amount or 0)
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"{value / 1_000_000:.0f}M"
    return f"{value:,.0f}"


def format_amount_abbrev(amount, currency: Optional[str] = None, *, signed: bool = False) -> str:
    """Abbreviated B/M form for large figures, e.g. "1.23B ₫" / "$1.23B"."""
    cfg = _config(currency if currency is not None else get_current_currency())
    value = float(amount or 0)
    sign = ""
    if signed:
        sign = "+" if value >= 0 else "-"
        value = abs(value)
    magnitude = abs(value)
    if magnitude >= 1_000_000_000:
        number = f"{value / 1_000_000_000:.2f}B"
    elif magnitude >= 1_000_000:
        number = f"{value / 1_000_000:.0f}M"
    else:
        number = f"{value:,.0f}"
    symbol = cfg["symbol"]
    body = f"{symbol}{number}" if cfg["position"] == "prefix" else f"{number} {symbol}"
    return f"{sign}{body}"


def jinja_currency(amount, signed: bool = False) -> str:
    return format_amount(amount, signed=signed)


def jinja_currency_abbrev(amount, signed: bool = False) -> str:
    return format_amount_abbrev(amount, signed=signed)


def jinja_currency_symbol() -> str:
    return _config(get_current_currency())["symbol"]


def register(env) -> None:
    """Register the currency filters/globals on a Jinja2 Environment."""
    env.filters["currency"] = jinja_currency
    env.filters["currency_abbrev"] = jinja_currency_abbrev
    env.filters["abbrev"] = abbreviate
    env.globals["currency_symbol"] = jinja_currency_symbol


def inject_currency(request) -> dict:
    """Context processor: makes `display_currency` available to every template."""
    return {"display_currency": get_current_currency()}
