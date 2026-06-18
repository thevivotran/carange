"""Tests for the live budget-preview fragment endpoint (Task 04)."""

from datetime import date

import pytest

from app.models.database import BudgetAllocation, Category, Transaction, TransactionType


@pytest.fixture()
def food_cat(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def unbudgeted_cat(db_session):
    cat = Category(name="Misc", type=TransactionType.EXPENSE, color="#999999", icon="question")
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


def test_budget_preview_shows_projected_state(client, db_session, food_cat):
    today = date.today()
    ym = f"{today.year:04d}-{today.month:02d}"
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, today, 1_000_000)

    resp = client.get(
        "/fragments/transactions/budget-preview",
        params={"category_id": food_cat.id, "amount": 2_000_000},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "After save:" in body
    assert "60.0%" in body
    assert "2,000,000" in body or "2.0M" in body or "2000K" in body
    assert "On track" in body


def test_budget_preview_empty_for_unbudgeted_category(client, unbudgeted_cat):
    resp = client.get(
        "/fragments/transactions/budget-preview",
        params={"category_id": unbudgeted_cat.id, "amount": 100_000},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == ""


def test_budget_preview_no_amount_shows_current_state(client, db_session, food_cat):
    """Selecting a budgeted category before entering an amount (amount omitted/0)
    must render the current state, not raise a 500 (regression)."""
    today = date.today()
    ym = f"{today.year:04d}-{today.month:02d}"
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, today, 1_000_000)

    resp = client.get(
        "/fragments/transactions/budget-preview",
        params={"category_id": food_cat.id},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "After save:" in body
    assert "20.0%" in body


def test_budget_preview_respects_date_param(client, db_session, food_cat):
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _add_expense(db_session, food_cat.id, date(2026, 5, 10), 1_000_000)

    resp = client.get(
        "/fragments/transactions/budget-preview",
        params={"category_id": food_cat.id, "amount": 1_000_000, "date": "2026-05-15"},
    )
    assert resp.status_code == 200
    body = resp.text
    assert "After save:" in body
    assert "40.0%" in body
