"""Tests for budget chip on transaction list rows (Task 02)."""

from datetime import date

import pytest

from app.models.database import (
    BudgetAllocation,
    Category,
    Transaction,
    TransactionType,
)


@pytest.fixture()
def food_cat(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def salary_cat(db_session):
    cat = Category(name="Salary", type=TransactionType.INCOME, color="#10B981", icon="money")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def _today_label(day: int = 1) -> str:
    from app.services.fiscal_period import current_period_label

    return current_period_label(date.today(), day)


def test_expense_row_shows_budget_chip(client, db_session, food_cat, salary_cat):
    label = _today_label()
    db_session.add(BudgetAllocation(category_id=food_cat.id, year_month=label, amount=5_000_000))
    db_session.commit()

    expense = Transaction(
        date=date.today(),
        amount=3_000_000,
        type=TransactionType.EXPENSE,
        category_id=food_cat.id,
        description="Lunch",
    )
    income = Transaction(
        date=date.today(),
        amount=10_000_000,
        type=TransactionType.INCOME,
        category_id=salary_cat.id,
        description="June salary",
    )
    db_session.add_all([expense, income])
    db_session.commit()

    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200

    assert "2,000,000" in r.text
    assert "left" in r.text
    assert "cg-badge" in r.text

    assert "Lunch" in r.text
    assert "June salary" in r.text


def test_income_row_no_budget_chip(client, db_session, salary_cat):
    income = Transaction(
        date=date.today(),
        amount=10_000_000,
        type=TransactionType.INCOME,
        category_id=salary_cat.id,
        description="Bonus",
    )
    db_session.add(income)
    db_session.commit()

    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Bonus" in r.text
    assert "cg-badge" not in r.text


def test_expense_without_budget_no_chip(client, db_session, food_cat):
    expense = Transaction(
        date=date.today(),
        amount=500_000,
        type=TransactionType.EXPENSE,
        category_id=food_cat.id,
        description="Coffee",
    )
    db_session.add(expense)
    db_session.commit()

    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Coffee" in r.text
    assert "cg-badge" not in r.text
