"""Tests for the transaction drawer budget-context fragment endpoint."""

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
def misc_cat(db_session):
    cat = Category(name="Misc", type=TransactionType.EXPENSE, color="#999999", icon="box")
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


def test_budget_context_with_budgeted_expense(client, db_session, food_cat):
    _add_alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    tx = _add_expense(db_session, food_cat.id, date(2026, 5, 10), 3_000_000)

    res = client.get(f"/fragments/transactions/{tx.id}/budget-context")
    assert res.status_code == 200
    body = res.text
    assert "On track" in body or "Watch" in body or "At risk" in body or "Over" in body
    assert "left" in body


def test_budget_context_no_budget(client, db_session, misc_cat):
    tx = _add_expense(db_session, misc_cat.id, date(2026, 5, 10), 100_000)

    res = client.get(f"/fragments/transactions/{tx.id}/budget-context")
    assert res.status_code == 200
    assert "No budget" in res.text


def test_budget_context_income_returns_muted(client, db_session):
    cat = Category(name="Salary", type=TransactionType.INCOME, color="#22c55e", icon="money-bill")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    tx = Transaction(
        date=date(2026, 5, 10),
        amount=10_000_000,
        type=TransactionType.INCOME,
        category_id=cat.id,
    )
    db_session.add(tx)
    db_session.commit()

    res = client.get(f"/fragments/transactions/{tx.id}/budget-context")
    assert res.status_code == 200
    assert "No budget" in res.text
