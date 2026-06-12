"""Shared recurring-cadence date stepping (used by scheduler + forecast)."""

from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

CADENCE_DELTA = {
    "daily": lambda d: d + timedelta(days=1),
    "weekly": lambda d: d + timedelta(weeks=1),
    "monthly": lambda d: d + relativedelta(months=1),
    "yearly": lambda d: d + relativedelta(years=1),
}


def iter_occurrences(cadence: str, first: date, until: date, *, max_iter: int = 400):
    """Yield occurrence dates starting at `first` (inclusive) through `until`
    (inclusive), stepping by `cadence`. Unknown cadence yields nothing.
    Capped at `max_iter` to avoid runaway loops."""
    step = CADENCE_DELTA.get(cadence)
    if step is None:
        return
    d = first
    n = 0
    while d <= until and n < max_iter:
        yield d
        d = step(d)
        n += 1
