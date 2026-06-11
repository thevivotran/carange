"""Fiscal period utilities — configurable month start day.

A fiscal period labeled "YYYY-MM" with configurable `day` (1-28) runs from
date(year, month, day) through the day before `day` in the following month.
When day == 1 this is byte-for-byte identical to calendar months.
"""

import calendar
from datetime import date, timedelta


SETTING_KEY = "month_start_day"
MIN_DAY = 1
MAX_DAY = 28


def _clamp_day(day: int, year: int, month: int) -> int:
    """Clamp day to the last valid day of (year, month)."""
    return min(day, calendar.monthrange(year, month)[1])


def get_month_start_day(db) -> int:
    """Read `month_start_day` from the Setting table; default/clamp to [1, 28]."""
    from app.services.settings_service import get_setting

    raw = get_setting(db, SETTING_KEY, str(MIN_DAY))
    try:
        day = int(raw)
    except (TypeError, ValueError):
        return MIN_DAY
    return max(MIN_DAY, min(MAX_DAY, day))


def fiscal_window_ym(year: int, month: int, day: int) -> tuple[date, date]:
    """Inclusive (start, end) window for a period labeled (year, month)."""
    start_day = _clamp_day(day, year, month)
    start = date(year, month, start_day)
    if month == 12:
        ny, nm = year + 1, 1
    else:
        ny, nm = year, month + 1
    end = date(ny, nm, _clamp_day(day, ny, nm)) - timedelta(days=1)
    return start, end


def fiscal_window(label: str, day: int) -> tuple[date, date]:
    """Inclusive window for a "YYYY-MM" labeled period."""
    year = int(label[:4])
    month = int(label[5:7])
    return fiscal_window_ym(year, month, day)


def current_period_ym(today: date, day: int) -> tuple[int, int]:
    """(year, month) of the fiscal period `today` falls inside."""
    clamped = _clamp_day(day, today.year, today.month)
    if today.day >= clamped:
        return today.year, today.month
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def current_period_label(today: date, day: int) -> str:
    """YYYY-MM label of the fiscal period `today` falls inside."""
    y, m = current_period_ym(today, day)
    return f"{y:04d}-{m:02d}"


def shift_period_ym(year: int, month: int, n: int) -> tuple[int, int]:
    """Shift a (year, month) by n whole periods (negative = earlier)."""
    idx = year * 12 + (month - 1) + n
    return idx // 12, idx % 12 + 1


def shift_period_label(label: str, n: int) -> str:
    """Shift a "YYYY-MM" label by n whole periods (negative = earlier)."""
    year = int(label[:4])
    month = int(label[5:7])
    y, m = shift_period_ym(year, month, n)
    return f"{y:04d}-{m:02d}"


def prev_period_label(label: str) -> str:
    """Label of the period immediately before `label`."""
    return shift_period_label(label, -1)


def days_in_period(label: str, day: int) -> int:
    """Inclusive length of a labeled period."""
    start, end = fiscal_window(label, day)
    return (end - start).days + 1


def day_index_in_period(today: date, day: int) -> int:
    """1-based position of `today` within the period it falls in."""
    y, m = current_period_ym(today, day)
    label = f"{y:04d}-{m:02d}"
    start, _ = fiscal_window(label, day)
    return (today - start).days + 1
