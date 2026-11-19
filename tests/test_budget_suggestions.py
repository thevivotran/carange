"""Budget suggestions: every active expense category gets a suggested monthly amount from
the average of the previous 3 fiscal months, so a first budget can be set in one step."""

from datetime import date, datetime, timezone

import pytest

from app.models.database import Category, Transaction, TransactionType
from app.services.budget_service import _shift_month, suggest_budget_amounts


@pytest.fixture()
def cats(db_session):
    food = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    fun = Category(name="Fun", type=TransactionType.EXPENSE, color="#8B5CF6", icon="star")
    hidden = Category(name="Old", type=TransactionType.EXPENSE, color="#999999", icon="x", is_active=False)
    salary = Category(name="Salary", type=TransactionType.INCOME, color="#10B981", icon="money")
    db_session.add_all([food, fun, hidden, salary])
    db_session.commit()
    return {"food": food, "fun": fun, "hidden": hidden, "salary": salary}


def _tx(db, cat, d, amount, type_=TransactionType.EXPENSE, deleted=False):
    db.add(
        Transaction(
            date=d,
            amount=amount,
            type=type_,
            category_id=cat.id,
            deleted_at=datetime.now(timezone.utc) if deleted else None,
        )
    )
    db.commit()


def test_shift_month_crosses_year_boundary():
    assert _shift_month("2026-01", -1) == "2025-12"
    assert _shift_month("2026-11", -3) == "2026-08"
    assert _shift_month("2026-12", 1) == "2027-01"


def test_average_of_previous_three_months_rounded_up(db_session, cats):
    _tx(db_session, cats["food"], date(2026, 8, 5), 3_000_000)
    _tx(db_session, cats["food"], date(2026, 9, 5), 3_000_000)
    _tx(db_session, cats["food"], date(2026, 10, 5), 3_100_000)
    # current month and anything older than the window are ignored
    _tx(db_session, cats["food"], date(2026, 11, 2), 99_000_000)
    _tx(db_session, cats["food"], date(2026, 7, 31), 99_000_000)
    out = suggest_budget_amounts(db_session, "2026-11", day=1)
    # (9,100,000 / 3) = 3,033,333 → rounded up to the next 50,000
    assert out[cats["food"].id] == 3_050_000


def test_categories_without_spending_suggest_zero_and_inactive_excluded(db_session, cats):
    out = suggest_budget_amounts(db_session, "2026-11", day=1)
    assert out[cats["fun"].id] == 0
    assert cats["hidden"].id not in out
    assert cats["salary"].id not in out


def test_deleted_and_income_transactions_ignored(db_session, cats):
    _tx(db_session, cats["fun"], date(2026, 10, 1), 600_000, deleted=True)
    _tx(db_session, cats["salary"], date(2026, 10, 1), 50_000_000, type_=TransactionType.INCOME)
    out = suggest_budget_amounts(db_session, "2026-11", day=1)
    assert out[cats["fun"].id] == 0


def test_suggestions_endpoint_shape(client, db_session, cats):
    _tx(db_session, cats["food"], date(2026, 10, 5), 1_500_000)
    res = client.get("/api/budget/2026-11/suggestions")
    assert res.status_code == 200
    rows = {r["category_name"]: r for r in res.json()}
    assert set(rows) >= {"Food", "Fun"}
    assert "Old" not in rows and "Salary" not in rows
    assert set(rows["Food"]) == {"category_id", "category_name", "category_color", "suggested"}
    # only one month in the window has data, so that month is the average
    assert rows["Food"]["suggested"] == 1_500_000


def test_empty_budget_offers_suggestions(client):
    html = client.get("/fragments/budget/rows?year_month=2026-11").text
    assert "Suggest budgets from last 3 months" in html
    assert "openEditModal(true)" in html


def test_average_uses_only_months_with_spending(db_session, cats):
    _tx(db_session, cats["food"], date(2026, 9, 10), 2_000_000)
    _tx(db_session, cats["food"], date(2026, 10, 10), 1_000_000)
    out = suggest_budget_amounts(db_session, "2026-11", day=1)
    assert out[cats["food"].id] == 1_500_000
