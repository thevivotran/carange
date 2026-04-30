"""Tests for budget rollover logic.

_compute_rows is the most complex function in the codebase: it accumulates
allocations and spending across months with a carry-forward envelope model.
"""
from datetime import date
import pytest

from app.models.database import BudgetAllocation, Category, Transaction, TransactionType
from app.routers.budget import _compute_rows, BASELINE


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def food_cat(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def transport_cat(db_session):
    cat = Category(name="Transport", type=TransactionType.EXPENSE, color="#F59E0B", icon="car")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def _add_alloc(db, category_id, year_month, amount):
    a = BudgetAllocation(category_id=category_id, year_month=year_month, amount=amount)
    db.add(a)
    db.commit()
    return a


def _add_expense(db, category_id, date_val, amount, income_cat_id=None):
    """Add an expense transaction. income_cat_id unused but kept for clarity."""
    t = Transaction(
        date=date_val, amount=amount,
        type=TransactionType.EXPENSE, category_id=category_id,
    )
    db.add(t)
    db.commit()
    return t


# ── Basic functionality ───────────────────────────────────────────────────────

def test_empty_budget_returns_empty_list(db_session):
    assert _compute_rows(db_session, "2026-05") == []


def test_before_baseline_returns_empty(db_session, food_cat):
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    assert _compute_rows(db_session, "2026-04") == []


def test_single_allocation_no_spending(db_session, food_cat):
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    rows = _compute_rows(db_session, "2026-05")
    assert len(rows) == 1
    r = rows[0]
    assert r["category_name"] == "Food"
    assert r["monthly_allocation"] == pytest.approx(5_000_000)
    assert r["cumulative_allocated"] == pytest.approx(5_000_000)
    assert r["cumulative_spent"] == pytest.approx(0)
    assert r["available_balance"] == pytest.approx(5_000_000)


def test_spending_reduces_available_balance(db_session, food_cat):
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 5, 10), 3_000_000)
    rows = _compute_rows(db_session, "2026-05")
    r = rows[0]
    assert r["available_balance"] == pytest.approx(2_000_000)
    assert r["this_month_spent"] == pytest.approx(3_000_000)


def test_over_budget_gives_negative_balance(db_session, food_cat):
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 5, 10), 7_000_000)
    rows = _compute_rows(db_session, "2026-05")
    assert rows[0]["available_balance"] == pytest.approx(-2_000_000)


# ── Rollover behaviour ────────────────────────────────────────────────────────

def test_unspent_budget_carries_forward(db_session, food_cat):
    """Unspent May budget should roll into June's available balance."""
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 5, 10), 2_000_000)  # 3M unspent

    rows = _compute_rows(db_session, "2026-06")
    r = rows[0]
    # Cumulative allocated: May 5M + June 5M = 10M (same allocation carries forward)
    assert r["cumulative_allocated"] == pytest.approx(10_000_000)
    assert r["cumulative_spent"] == pytest.approx(2_000_000)
    assert r["available_balance"] == pytest.approx(8_000_000)


def test_deficit_carries_forward(db_session, food_cat):
    """Overspend in May reduces June's available balance."""
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 5, 10), 7_000_000)  # -2M deficit

    rows = _compute_rows(db_session, "2026-06")
    r = rows[0]
    assert r["cumulative_allocated"] == pytest.approx(10_000_000)
    assert r["cumulative_spent"] == pytest.approx(7_000_000)
    assert r["available_balance"] == pytest.approx(3_000_000)


def test_allocation_change_applies_from_new_month(db_session, food_cat):
    """A new allocation record in June overrides May's amount for June onward."""
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_alloc(db_session, food_cat.id, "2026-06", 8_000_000)

    rows = _compute_rows(db_session, "2026-06")
    r = rows[0]
    assert r["monthly_allocation"] == pytest.approx(8_000_000)
    # Cumulative: 5M (May) + 8M (June) = 13M
    assert r["cumulative_allocated"] == pytest.approx(13_000_000)


def test_spending_outside_date_range_not_counted(db_session, food_cat):
    """Transactions before BASELINE should not affect cumulative_spent."""
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 4, 30), 3_000_000)  # before baseline

    rows = _compute_rows(db_session, "2026-05")
    assert rows[0]["cumulative_spent"] == pytest.approx(0)


def test_two_categories_independent(db_session, food_cat, transport_cat):
    """Two categories are tracked independently."""
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_alloc(db_session, transport_cat.id, "2026-05", 2_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 5, 5), 4_000_000)
    _add_expense(db_session, transport_cat.id, date(2026, 5, 6), 3_000_000)

    rows = {r["category_name"]: r for r in _compute_rows(db_session, "2026-05")}
    assert rows["Food"]["available_balance"] == pytest.approx(1_000_000)
    assert rows["Transport"]["available_balance"] == pytest.approx(-1_000_000)


def test_this_month_spent_only_current_month(db_session, food_cat):
    """this_month_spent counts only the queried month, not prior months."""
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 5, 1), 2_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 6, 1), 1_500_000)

    rows = _compute_rows(db_session, "2026-06")
    r = rows[0]
    assert r["this_month_spent"] == pytest.approx(1_500_000)
    assert r["cumulative_spent"] == pytest.approx(3_500_000)
