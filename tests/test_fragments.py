"""Tests for HTMX fragment endpoints under /fragments/."""

import pytest
from datetime import date
from app.models.database import Transaction, TransactionType, Category


@pytest.fixture()
def category(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def sample_transaction(db_session, category):
    tx = Transaction(
        date=date.today(),
        amount=100000,
        type=TransactionType.EXPENSE,
        category_id=category.id,
        description="Lunch",
        source="manual",
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    return tx


def test_fragment_list_empty(client):
    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "No transactions found" in r.text


def test_fragment_list_with_data(client, sample_transaction):
    r = client.get("/fragments/transactions/list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Lunch" in r.text
    assert "₫" in r.text


def test_fragment_list_filter_by_type(client, sample_transaction):
    r = client.get("/fragments/transactions/list?type=income", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No transactions found" in r.text


def test_fragment_list_trash_mode(client, sample_transaction):
    r = client.get("/fragments/transactions/list?trash=true", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No transactions found" in r.text  # sample is not deleted


def test_fragment_list_pagination(client):
    r = client.get("/fragments/transactions/list?skip=0&limit=20", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_summary(client):
    r = client.get("/fragments/transactions/summary", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Income" in r.text
    assert "₫" in r.text


def test_fragment_history_no_logs(client, sample_transaction):
    r = client.get(
        f"/fragments/transactions/{sample_transaction.id}/history",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "No changes recorded" in r.text


def test_fragment_history_nonexistent(client):
    r = client.get("/fragments/transactions/99999/history", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No changes recorded" in r.text


# ── Dashboard fragment tests ──────────────────────────────────────────────────


def test_fragment_dashboard_safety_score(client):
    r = client.get("/fragments/dashboard/safety-score", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Family Safety Score" in r.text


def test_fragment_dashboard_safety_score_with_month(client):
    r = client.get("/fragments/dashboard/safety-score?year=2025&month=4", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Family Safety Score" in r.text


def test_fragment_dashboard_kpi_cards(client):
    r = client.get("/fragments/dashboard/kpi-cards", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Net Worth" in r.text


def test_fragment_dashboard_kpi_cards_with_month(client):
    r = client.get("/fragments/dashboard/kpi-cards?year=2025&month=4", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Net Worth" in r.text


def test_fragment_dashboard_settings_form(client):
    r = client.get("/fragments/dashboard/settings-form", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "savings_target_pct" in r.text


def test_fragment_dashboard_settings_post(client):
    r = client.post(
        "/fragments/dashboard/settings",
        data={"savings_target_pct": "30", "fi_target_vnd": "", "baby_fund_bundle_id": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers


# ── Budget fragment tests ─────────────────────────────────────────────────────


def test_fragment_budget_rows_default(client):
    r = client.get("/fragments/budget/rows", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "budget-rows-region" in r.text


def test_fragment_budget_rows_explicit_month(client):
    r = client.get("/fragments/budget/rows?year_month=2025-01", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "budget-rows-region" in r.text


def test_fragment_budget_rows_empty(client):
    r = client.get("/fragments/budget/rows?year_month=2000-01", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No budget set for this month" in r.text


# ── Savings fragment tests ────────────────────────────────────────────────────


def test_fragment_savings_grid_default(client):
    r = client.get("/fragments/savings/grid", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "savings-grid-region" in r.text


def test_fragment_savings_grid_empty(client):
    r = client.get("/fragments/savings/grid?status=active", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No savings bundles yet" in r.text


def test_fragment_savings_bundle_transactions_empty(client, db_session):
    from datetime import date as _date

    from app.models.database import SavingsBundle, SavingsStatus, SavingsType

    bundle = SavingsBundle(
        name="Test Bundle",
        bank_name="VCB",
        type=SavingsType.FIXED_DEPOSIT,
        initial_deposit=10_000_000,
        current_amount=10_000_000,
        future_amount=10_500_000,
        start_date=_date(2025, 1, 1),
        status=SavingsStatus.ACTIVE,
    )
    db_session.add(bundle)
    db_session.commit()
    db_session.refresh(bundle)
    r = client.get(f"/fragments/savings/{bundle.id}/transactions", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No linked transactions" in r.text


# ── Projects fragment tests ───────────────────────────────────────────────────


def test_fragment_projects_grid_default(client):
    r = client.get("/fragments/projects/grid", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "projects-grid-region" in r.text


def test_fragment_projects_grid_empty(client):
    r = client.get("/fragments/projects/grid?status=planning", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No projects yet" in r.text


# ── Categories fragment tests ─────────────────────────────────────────────────


def test_fragment_categories_rows_expense(client):
    r = client.get("/fragments/categories/rows?type=expense", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fragment_categories_rows_income(client):
    r = client.get("/fragments/categories/rows?type=income", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fragment_categories_rows_sort(client):
    url = "/fragments/categories/rows?type=expense&sort_col=count&sort_dir=desc"
    r = client.get(url, headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_categories_rows_empty_type(client):
    r = client.get("/fragments/categories/rows?type=expense", headers={"HX-Request": "true"})
    assert r.status_code == 200


# ── Import fragment tests ─────────────────────────────────────────────────────


def test_fragment_import_jobs_empty(client):
    r = client.get("/fragments/import/jobs", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fragment_import_jobs_filter(client):
    r = client.get("/fragments/import/jobs?status=done", headers={"HX-Request": "true"})
    assert r.status_code == 200


def test_fragment_import_job_transactions_nonexistent(client):
    r = client.get("/fragments/import/99999/transactions", headers={"HX-Request": "true"})
    assert r.status_code == 200


# ── Templates fragment tests ──────────────────────────────────────────────────


def test_fragment_templates_rows_default(client):
    r = client.get("/fragments/templates/rows", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_fragment_templates_rows_filter(client):
    r = client.get(
        "/fragments/templates/rows?type=expense&is_active=true",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200


def test_fragment_templates_rows_empty_category_id_no_error(client):
    # empty string category_id must not return 422
    r = client.get(
        "/fragments/templates/rows?category_id=",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200


def test_fragment_templates_rows_is_active_false(client):
    r = client.get(
        "/fragments/templates/rows?is_active=false",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200


def test_fragment_templates_rows_with_template(client, db_session):
    from app.models.database import Category, TransactionTemplate, TransactionType

    cat = Category(name="Groceries", type=TransactionType.EXPENSE, color="#EF4444", icon="cart")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)

    tpl = TransactionTemplate(name="Weekly Groceries", amount=500_000, type=TransactionType.EXPENSE, category_id=cat.id)
    db_session.add(tpl)
    db_session.commit()

    r = client.get(
        f"/fragments/templates/rows?category_id={cat.id}",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "Weekly Groceries" in r.text


# ── Assets fragment tests ─────────────────────────────────────────────────────


def test_fragment_assets_grid_default(client):
    r = client.get("/fragments/assets/grid", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "assets-grid-region" in r.text


def test_fragment_assets_grid_empty(client):
    r = client.get("/fragments/assets/grid?asset_type=gold", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "No assets yet" in r.text
