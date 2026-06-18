"""Tests for the shared budget_context helper."""

from datetime import date

import pytest

from app.models.database import BudgetAllocation, Category, Transaction, TransactionType
from app.services.budget_context import (
    budget_snapshot,
    pace,
    pace_label,
    render_bar,
    status_word,
)


# ── Pure-function tests (no DB) ──────────────────────────────────────────────


class TestStatusWord:
    def test_on_track(self):
        assert status_word(50.0, 500) == "On track"

    def test_on_track_boundary(self):
        assert status_word(74.9, 251) == "On track"

    def test_watch(self):
        assert status_word(75.0, 250) == "Watch"

    def test_watch_upper(self):
        assert status_word(94.9, 51) == "Watch"

    def test_at_risk(self):
        assert status_word(95.0, 50) == "At risk"

    def test_at_risk_upper(self):
        assert status_word(99.9, 1) == "At risk"

    def test_over_by_pct(self):
        assert status_word(100.0, 0) == "Over"

    def test_over_by_negative_left(self):
        assert status_word(80.0, -1) == "Over"


class TestPaceLabel:
    def test_on_pace(self):
        assert pace_label(50.0, 50.0) == "On pace"

    def test_on_pace_boundary(self):
        assert pace_label(55.0, 50.0) == "On pace"

    def test_ahead_of_pace(self):
        assert pace_label(70.0, 50.0) == "Ahead of pace"

    def test_ahead_of_pace_boundary(self):
        assert pace_label(70.0, 50.0) == "Ahead of pace"

    def test_well_ahead(self):
        assert pace_label(80.0, 50.0) == "Well ahead"

    def test_gap_exactly_5(self):
        assert pace_label(55.0, 50.0) == "On pace"

    def test_gap_exactly_20(self):
        assert pace_label(70.0, 50.0) == "Ahead of pace"

    def test_gap_just_over_5(self):
        assert pace_label(55.1, 50.0) == "Ahead of pace"

    def test_gap_just_over_20(self):
        assert pace_label(70.1, 50.0) == "Well ahead"


class TestRenderBar:
    def test_zero(self):
        bar = render_bar(0)
        assert bar == "░" * 10

    def test_hundred(self):
        bar = render_bar(100)
        assert bar == "█" * 10

    def test_eighty_two(self):
        bar = render_bar(82)
        assert bar == "█" * 8 + "░" * 2

    def test_clamped_above(self):
        bar = render_bar(150)
        assert bar == "█" * 10

    def test_clamped_below(self):
        bar = render_bar(-10)
        assert bar == "░" * 10

    def test_custom_width(self):
        bar = render_bar(50, width=20)
        assert bar == "█" * 10 + "░" * 10


class TestPace:
    def test_before_start(self):
        pct, _ = pace("2026-05", 1, date(2026, 4, 1))
        assert pct == 0.0

    def test_after_end(self):
        pct, _ = pace("2026-05", 1, date(2026, 6, 2))
        assert pct == 100.0

    def test_mid_period(self):
        pct, _ = pace("2026-05", 1, date(2026, 5, 16))
        assert pct == pytest.approx(51.6, abs=0.1)


# ── DB-backed tests ──────────────────────────────────────────────────────────


@pytest.fixture()
def food_cat(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def _add_alloc(db, category_id, year_month, amount):
    a = BudgetAllocation(category_id=category_id, year_month=year_month, amount=amount)
    db.add(a)
    db.commit()
    return a


def _add_expense(db, category_id, date_val, amount):
    t = Transaction(
        date=date_val,
        amount=amount,
        type=TransactionType.EXPENSE,
        category_id=category_id,
    )
    db.add(t)
    db.commit()
    return t


class TestBudgetSnapshot:
    def test_no_allocation_returns_none(self, db_session, food_cat):
        assert budget_snapshot(db_session, food_cat.id, "2026-05", day=1) is None

    def test_basic_snapshot(self, db_session, food_cat):
        _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
        _add_expense(db_session, food_cat.id, date(2026, 5, 10), 3_000_000)
        snap = budget_snapshot(db_session, food_cat.id, "2026-05", day=1)
        assert snap is not None
        assert snap["category_id"] == food_cat.id
        assert snap["category_name"] == "Food"
        assert snap["allocated"] == pytest.approx(5_000_000)
        assert snap["spent"] == pytest.approx(3_000_000)
        assert snap["left"] == pytest.approx(2_000_000)
        assert snap["usage_pct"] == pytest.approx(60.0)
        assert snap["status"] == "On track"

    def test_extra_amount_projection(self, db_session, food_cat):
        _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
        _add_expense(db_session, food_cat.id, date(2026, 5, 10), 3_000_000)
        snap = budget_snapshot(db_session, food_cat.id, "2026-05", extra_amount=1_500_000, day=1)
        assert snap is not None
        assert "projected_spent" in snap
        assert snap["projected_spent"] == pytest.approx(4_500_000)
        assert snap["projected_left"] == pytest.approx(500_000)
        assert snap["projected_usage_pct"] == pytest.approx(90.0)
        assert snap["projected_status"] == "Watch"

    def test_extra_amount_pushes_over(self, db_session, food_cat):
        _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
        _add_expense(db_session, food_cat.id, date(2026, 5, 10), 4_000_000)
        snap = budget_snapshot(db_session, food_cat.id, "2026-05", extra_amount=2_000_000, day=1)
        assert snap["projected_spent"] == pytest.approx(6_000_000)
        assert snap["projected_left"] == pytest.approx(-1_000_000)
        assert snap["projected_status"] == "Over"

    def test_zero_allocation_returns_none(self, db_session, food_cat):
        _add_alloc(db_session, food_cat.id, "2026-05", 0)
        assert budget_snapshot(db_session, food_cat.id, "2026-05", day=1) is None
