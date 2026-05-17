"""Extended dashboard tests — API endpoints not covered by direct function tests."""

import pytest
from datetime import date

from app.models.database import Transaction, Category, TransactionType


# ── /dashboard/summary ────────────────────────────────────────────────────────


def test_dashboard_summary_returns_schema(client):
    r = client.get("/api/dashboard/summary")
    assert r.status_code == 200
    d = r.json()
    assert "net_worth" in d
    assert "savings_rate" in d
    assert "total_income_month" in d
    assert "total_expense_month" in d


def test_dashboard_summary_with_year_month_params(client):
    r = client.get("/api/dashboard/summary?year=2025&month=1")
    assert r.status_code == 200
    d = r.json()
    assert d["total_income_month"] == pytest.approx(0)
    assert d["total_expense_month"] == pytest.approx(0)


def test_dashboard_summary_with_transactions(client, db_session):
    inc_cat = Category(name="Salary", type=TransactionType.INCOME, color="#10B981", icon="money")
    exp_cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add_all([inc_cat, exp_cat])
    db_session.commit()

    db_session.add_all(
        [
            Transaction(date=date(2026, 5, 1), amount=10_000_000, type=TransactionType.INCOME, category_id=inc_cat.id),
            Transaction(date=date(2026, 5, 2), amount=2_000_000, type=TransactionType.EXPENSE, category_id=exp_cat.id),
        ]
    )
    db_session.commit()

    r = client.get("/api/dashboard/summary?year=2026&month=5")
    assert r.status_code == 200
    d = r.json()
    assert d["total_income_month"] == pytest.approx(10_000_000)
    assert d["total_expense_month"] == pytest.approx(2_000_000)


# ── /dashboard/monthly-trend ──────────────────────────────────────────────────


def test_monthly_trend_returns_12_months(client):
    r = client.get("/api/dashboard/monthly-trend")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 12


def test_monthly_trend_shape(client):
    r = client.get("/api/dashboard/monthly-trend")
    assert r.status_code == 200
    for entry in r.json():
        assert "month" in entry
        assert "income" in entry
        assert "expense" in entry
        assert "savings" in entry
        assert "net" in entry
        assert "savings_rate" in entry


def test_monthly_trend_with_data(client, db_session):
    inc_cat = Category(name="Salary2", type=TransactionType.INCOME, color="#10B981", icon="money")
    exp_cat = Category(name="Rent2", type=TransactionType.EXPENSE, color="#EF4444", icon="home")
    db_session.add_all([inc_cat, exp_cat])
    db_session.commit()

    db_session.add(
        Transaction(
            date=date.today().replace(day=1),
            amount=15_000_000,
            type=TransactionType.INCOME,
            category_id=inc_cat.id,
        )
    )
    db_session.commit()

    r = client.get("/api/dashboard/monthly-trend")
    assert r.status_code == 200
    this_month = r.json()[-1]
    assert this_month["income"] == pytest.approx(15_000_000)


# ── /dashboard/expense-by-category ───────────────────────────────────────────


def test_expense_by_category_empty(client):
    r = client.get("/api/dashboard/expense-by-category?year=2020&month=1")
    assert r.status_code == 200
    assert r.json() == []


def test_expense_by_category_with_data(client, db_session):
    cat = Category(name="Groceries", type=TransactionType.EXPENSE, color="#F59E0B", icon="cart")
    db_session.add(cat)
    db_session.commit()

    db_session.add(
        Transaction(
            date=date(2026, 5, 10),
            amount=3_000_000,
            type=TransactionType.EXPENSE,
            category_id=cat.id,
        )
    )
    db_session.commit()

    r = client.get("/api/dashboard/expense-by-category?year=2026&month=5")
    assert r.status_code == 200
    result = r.json()
    assert len(result) >= 1
    assert result[0]["category_name"] == "Groceries"
    assert result[0]["total"] == pytest.approx(3_000_000)
    assert result[0]["percentage"] == pytest.approx(100.0)


def test_expense_by_category_defaults_to_current_month(client):
    r = client.get("/api/dashboard/expense-by-category")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── /dashboard/wealth-building-trend ─────────────────────────────────────────


def test_wealth_building_trend_returns_6_months(client):
    r = client.get("/api/dashboard/wealth-building-trend")
    assert r.status_code == 200
    assert len(r.json()) == 6


def test_wealth_building_trend_shape(client):
    r = client.get("/api/dashboard/wealth-building-trend")
    assert r.status_code == 200
    for entry in r.json():
        assert "month" in entry
        assert "tiet_kiem" in entry
        assert "bds" in entry
        assert "total" in entry


def test_wealth_building_trend_with_savings_category(client, db_session):
    tk_cat = Category(
        name="Tiết kiệm", type=TransactionType.EXPENSE, color="#3B82F6", icon="piggy-bank", is_wealth_building=True
    )
    db_session.add(tk_cat)
    db_session.commit()

    db_session.add(
        Transaction(
            date=date.today().replace(day=1),
            amount=5_000_000,
            type=TransactionType.EXPENSE,
            category_id=tk_cat.id,
            is_savings_related=True,
        )
    )
    db_session.commit()

    r = client.get("/api/dashboard/wealth-building-trend")
    assert r.status_code == 200
    this_month = r.json()[-1]
    assert this_month["tiet_kiem"] == pytest.approx(5_000_000)
