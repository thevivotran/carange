from datetime import date

from app.services.cadence import iter_occurrences


def test_monthly_stepping_inclusive_sequence():
    result = list(iter_occurrences("monthly", date(2026, 1, 31), date(2026, 4, 1)))
    assert result == [
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 28),
    ]


def test_first_after_until_yields_nothing():
    result = list(iter_occurrences("monthly", date(2026, 5, 1), date(2026, 1, 1)))
    assert result == []


def test_unknown_cadence_yields_nothing():
    result = list(iter_occurrences("biweekly", date(2026, 1, 1), date(2026, 12, 31)))
    assert result == []


def test_max_iter_cap_respected():
    result = list(iter_occurrences("daily", date(2020, 1, 1), date(2030, 1, 1), max_iter=400))
    assert len(result) == 400
