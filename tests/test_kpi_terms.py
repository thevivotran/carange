"""Tests for the KPI terms (category bucket) feature."""

from datetime import date
import pytest

from app.models.database import Category, Transaction, TransactionType
from app.services.dashboard_service import get_kpi_role_category_ids, get_dashboard_data

THIS_YEAR = date.today().year
THIS_MONTH = date.today().month


# ── Resolver tests ───────────────────────────────────────────────────────────


class TestGetKpiRoleCategoryIds:
    def test_empty_db(self, db_session):
        result = get_kpi_role_category_ids(db_session)
        assert result == {"liquid_savings": [], "real_estate": []}

    def test_returns_both_roles(self, db_session):
        ls = Category(name="LS", type=TransactionType.EXPENSE, kpi_role="liquid_savings")
        re = Category(name="RE", type=TransactionType.EXPENSE, kpi_role="real_estate")
        db_session.add_all([ls, re])
        db_session.commit()

        result = get_kpi_role_category_ids(db_session)
        assert ls.id in result["liquid_savings"]
        assert re.id in result["real_estate"]

    def test_ignores_unassigned(self, db_session):
        none_cat = Category(name="Food", type=TransactionType.EXPENSE, kpi_role=None)
        db_session.add(none_cat)
        db_session.commit()

        result = get_kpi_role_category_ids(db_session)
        assert none_cat.id not in result["liquid_savings"]
        assert none_cat.id not in result["real_estate"]

    def test_ignores_income_categories(self, db_session):
        income_ls = Category(name="Income LS", type=TransactionType.INCOME, kpi_role="liquid_savings")
        db_session.add(income_ls)
        db_session.commit()

        result = get_kpi_role_category_ids(db_session)
        assert income_ls.id not in result["liquid_savings"]

    def test_multiple_categories_per_role(self, db_session):
        cats = [Category(name=f"Savings{i}", type=TransactionType.EXPENSE, kpi_role="liquid_savings") for i in range(3)]
        db_session.add_all(cats)
        db_session.commit()

        result = get_kpi_role_category_ids(db_session)
        assert len(result["liquid_savings"]) == 3
        assert all(c.id in result["liquid_savings"] for c in cats)


# ── KPI dashboard computation tests ──────────────────────────────────────────


def _summary(db_session):
    return get_dashboard_data(db_session, year=THIS_YEAR, month=THIS_MONTH)["summary"]


def make_transaction(db, date_val, amount, type_, category_id, **kw):
    db.add(
        Transaction(
            date=date_val,
            amount=amount,
            type=type_,
            category_id=category_id,
            **kw,
        )
    )
    db.commit()


class TestKpiComputation:
    def test_savings_rate_with_liquid_savings(self, db_session):
        ls = Category(name="My Savings", type=TransactionType.EXPENSE, kpi_role="liquid_savings")
        inc = Category(name="Salary", type=TransactionType.INCOME)
        db_session.add_all([ls, inc])
        db_session.commit()

        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 1),
            amount=100_000_000,
            type_=TransactionType.INCOME,
            category_id=inc.id,
        )
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 2),
            amount=30_000_000,
            type_=TransactionType.EXPENSE,
            category_id=ls.id,
        )

        s = _summary(db_session)
        assert s["savings_rate"] == pytest.approx(30.0, rel=1e-2)
        assert s["liquid_savings_rate"] == pytest.approx(30.0, rel=1e-2)

    def test_savings_rate_with_real_estate(self, db_session):
        re = Category(name="Property", type=TransactionType.EXPENSE, kpi_role="real_estate")
        inc = Category(name="Salary", type=TransactionType.INCOME)
        db_session.add_all([re, inc])
        db_session.commit()

        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 1),
            amount=100_000_000,
            type_=TransactionType.INCOME,
            category_id=inc.id,
        )
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 2),
            amount=20_000_000,
            type_=TransactionType.EXPENSE,
            category_id=re.id,
        )

        s = _summary(db_session)
        assert s["savings_rate"] == pytest.approx(20.0, rel=1e-2)
        assert s["bds_rate"] == pytest.approx(20.0, rel=1e-2)

    def test_living_expense_excludes_bucket_categories(self, db_session):
        ls = Category(name="Savings", type=TransactionType.EXPENSE, kpi_role="liquid_savings")
        re = Category(name="Real Estate", type=TransactionType.EXPENSE, kpi_role="real_estate")
        food = Category(name="Food", type=TransactionType.EXPENSE)
        inc = Category(name="Salary", type=TransactionType.INCOME)
        db_session.add_all([ls, re, food, inc])
        db_session.commit()

        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 1),
            amount=100_000_000,
            type_=TransactionType.INCOME,
            category_id=inc.id,
        )
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 2),
            amount=40_000_000,
            type_=TransactionType.EXPENSE,
            category_id=ls.id,
        )
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 3),
            amount=30_000_000,
            type_=TransactionType.EXPENSE,
            category_id=re.id,
        )
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 4),
            amount=20_000_000,
            type_=TransactionType.EXPENSE,
            category_id=food.id,
        )

        s = _summary(db_session)
        # Living expense = only food (20M), not savings (40M) or real estate (30M)
        assert s["total_expense"] == pytest.approx(20_000_000)
        assert s["living_expense_ratio"] == pytest.approx(20.0, rel=1e-2)
        # Savings rate = (40M + 30M) / 100M = 70%
        assert s["savings_rate"] == pytest.approx(70.0, rel=1e-2)

    def test_net_cash_counts_non_savings_bucket_spend(self, db_session):
        """A bucket-category expense with is_savings_related=False still reduces net
        cash — it must not vanish from both the living-expense and savings buckets."""
        ls = Category(name="Savings", type=TransactionType.EXPENSE, kpi_role="liquid_savings")
        inc = Category(name="Salary", type=TransactionType.INCOME)
        db_session.add_all([ls, inc])
        db_session.commit()

        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 1),
            amount=100_000_000,
            type_=TransactionType.INCOME,
            category_id=inc.id,
        )
        # Liquid-savings expense, NOT flagged is_savings_related — the regression case.
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 2),
            amount=30_000_000,
            type_=TransactionType.EXPENSE,
            category_id=ls.id,
            is_savings_related=False,
        )

        s = _summary(db_session)
        # Net cash must reflect the 30M outflow: 100M - 30M = 70M.
        assert s["net_this_month"] == pytest.approx(70_000_000)
        # Living expense display still excludes the bucket category.
        assert s["total_expense"] == pytest.approx(0)
        # And it still counts toward the savings rate.
        assert s["savings_rate"] == pytest.approx(30.0, rel=1e-2)

    def test_no_bucket_categories_yields_zero_savings_rate(self, db_session):
        inc = Category(name="Salary", type=TransactionType.INCOME)
        food = Category(name="Food", type=TransactionType.EXPENSE)
        db_session.add_all([inc, food])
        db_session.commit()

        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 1),
            amount=100_000_000,
            type_=TransactionType.INCOME,
            category_id=inc.id,
        )
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 2),
            amount=50_000_000,
            type_=TransactionType.EXPENSE,
            category_id=food.id,
        )

        s = _summary(db_session)
        assert s["savings_rate"] == 0
        assert s["liquid_savings_rate"] == 0
        assert s["bds_rate"] == 0
        # Monthly expense should equal food spending
        assert s["total_expense"] == pytest.approx(50_000_000)


# ── Settings endpoint tests ──────────────────────────────────────────────────


class TestSaveKpiTerms:
    def test_assign_liquid_savings_role(self, client, db_session):
        cat = Category(name="Savings", type=TransactionType.EXPENSE)
        db_session.add(cat)
        db_session.commit()

        r = client.post("/settings/kpi-terms", data={f"role_{cat.id}": "liquid_savings"})
        assert r.status_code == 200

        db_session.refresh(cat)
        assert cat.kpi_role == "liquid_savings"

    def test_assign_real_estate_role(self, client, db_session):
        cat = Category(name="Property", type=TransactionType.EXPENSE)
        db_session.add(cat)
        db_session.commit()

        r = client.post("/settings/kpi-terms", data={f"role_{cat.id}": "real_estate"})
        assert r.status_code == 200

        db_session.refresh(cat)
        assert cat.kpi_role == "real_estate"

    def test_clear_role_to_none(self, client, db_session):
        cat = Category(name="Property", type=TransactionType.EXPENSE, kpi_role="real_estate")
        db_session.add(cat)
        db_session.commit()

        r = client.post("/settings/kpi-terms", data={f"role_{cat.id}": ""})
        assert r.status_code == 200

        db_session.refresh(cat)
        assert cat.kpi_role is None

    def test_invalid_role_value_is_cleared(self, client, db_session):
        cat = Category(name="Savings", type=TransactionType.EXPENSE, kpi_role="liquid_savings")
        db_session.add(cat)
        db_session.commit()

        r = client.post("/settings/kpi-terms", data={f"role_{cat.id}": "bogus_role"})
        assert r.status_code == 200

        db_session.refresh(cat)
        assert cat.kpi_role is None

    def test_settings_page_shows_kpi_categories(self, client, db_session):
        db_session.add(Category(name="Food", type=TransactionType.EXPENSE))
        db_session.add(Category(name="Savings", type=TransactionType.EXPENSE, kpi_role="liquid_savings"))
        db_session.commit()

        r = client.get("/settings")
        assert r.status_code == 200
        assert b"KPI Categories" in r.content
        assert b"Savings" in r.content
        assert b"liquid_savings" in r.content

    def test_updates_affect_dashboard_kpis(self, client, db_session):
        """Assign role via settings endpoint, then verify dashboard picks it up."""
        inc = Category(name="Salary", type=TransactionType.INCOME)
        cat = Category(name="Savings", type=TransactionType.EXPENSE)
        db_session.add_all([inc, cat])
        db_session.commit()

        # Before role assignment: savings rate is 0
        data = get_dashboard_data(db_session, year=THIS_YEAR, month=THIS_MONTH)
        assert data["summary"]["savings_rate"] == 0

        # Make a transaction
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 1),
            amount=100_000_000,
            type_=TransactionType.INCOME,
            category_id=inc.id,
        )
        make_transaction(
            db_session,
            date(THIS_YEAR, THIS_MONTH, 2),
            amount=25_000_000,
            type_=TransactionType.EXPENSE,
            category_id=cat.id,
        )

        # Assign role
        r = client.post("/settings/kpi-terms", data={f"role_{cat.id}": "liquid_savings"})
        assert r.status_code == 200

        # Dashboard should now show savings rate = 25%
        data = get_dashboard_data(db_session, year=THIS_YEAR, month=THIS_MONTH)
        assert data["summary"]["savings_rate"] == pytest.approx(25.0, rel=1e-2)
