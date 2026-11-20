"""One display format for dates, shared by every Jinja environment.

    {{ d | app_date }}          → "19 Nov 2026"
    {{ d | app_date('long') }}  → "Thu, 19 Nov 2026"
    {{ d | app_date('short') }} → "19 Nov"

The JS twin is `formatDate()` in base.html. Native `<input type="date">` pickers keep the
browser's own format; dialogs show an app-format hint beside them.
"""

from __future__ import annotations

from datetime import date, datetime

_FORMATS = {
    "default": "{d} {b} {Y}",
    "long": "{a}, {d} {b} {Y}",
    "short": "{d} {b}",
}


def app_date(value: date | datetime | str | None, style: str = "default") -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return _FORMATS.get(style, _FORMATS["default"]).format(
        d=value.day, b=value.strftime("%b"), Y=value.year, a=value.strftime("%a")
    )


def register(env) -> None:
    env.filters["app_date"] = app_date
