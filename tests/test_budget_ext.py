"""Extended budget tests — HTTP endpoints not covered by direct _compute_rows tests."""

import pytest
from datetime import date

from app.models.database import BudgetAllocation, Category, Transaction, TransactionType


@pytest.fixture()
def food_cat(db_session):
    cat = Category(name="FoodBE", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def transport_cat(db_session):
    cat = Category(name="TransportBE", type=TransactionType.EXPENSE, color="#F59E0B", icon="car")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def _alloc(db, category_id, year_month, amount):
    a = BudgetAllocation(category_id=category_id, year_month=year_month, amount=amount)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# ── GET /{year_month}/rows ────────────────────────────────────────────────────


def test_get_budget_rows_empty(client):
    r = client.get("/api/budget/2026-05/rows")
    assert r.status_code == 200
    assert r.json() == []


def test_get_budget_rows_with_allocation(client, db_session, food_cat):
    _alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    r = client.get("/api/budget/2026-05/rows")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["category_name"] == "FoodBE"
    assert rows[0]["monthly_allocation"] == pytest.approx(5_000_000)


def test_get_budget_rows_before_baseline_returns_empty(client, db_session, food_cat):
    _alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    r = client.get("/api/budget/2026-04/rows")
    assert r.status_code == 200
    assert r.json() == []


# ── GET /allocations/{year_month} ─────────────────────────────────────────────


def test_get_allocations_for_month_empty(client):
    r = client.get("/api/budget/allocations/2026-05")
    assert r.status_code == 200
    assert r.json() == []


def test_get_allocations_for_month_with_data(client, db_session, food_cat):
    _alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    r = client.get("/api/budget/allocations/2026-05")
    assert r.status_code == 200
    result = r.json()
    assert len(result) == 1
    assert result[0]["category_id"] == food_cat.id
    assert result[0]["amount"] == pytest.approx(5_000_000)


# ── GET /categories/unbudgeted/{year_month} ───────────────────────────────────


def test_get_unbudgeted_categories_all_unbudgeted(client, db_session, food_cat, transport_cat):
    r = client.get("/api/budget/categories/unbudgeted/2026-05")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert food_cat.id in ids
    assert transport_cat.id in ids


def test_get_unbudgeted_categories_excludes_budgeted(client, db_session, food_cat, transport_cat):
    _alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    r = client.get("/api/budget/categories/unbudgeted/2026-05")
    assert r.status_code == 200
    ids = [c["id"] for c in r.json()]
    assert food_cat.id not in ids
    assert transport_cat.id in ids


# ── POST / (set allocation — create + upsert) ────────────────────────────────


def test_set_allocation_creates_new(client, food_cat):
    r = client.post(
        "/api/budget/",
        json={"category_id": food_cat.id, "year_month": "2026-05", "amount": 5_000_000},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["category_id"] == food_cat.id
    assert d["amount"] == pytest.approx(5_000_000)
    assert d["year_month"] == "2026-05"


def test_set_allocation_upserts_existing(client, food_cat):
    client.post("/api/budget/", json={"category_id": food_cat.id, "year_month": "2026-05", "amount": 5_000_000})
    r = client.post("/api/budget/", json={"category_id": food_cat.id, "year_month": "2026-05", "amount": 8_000_000})
    assert r.status_code == 200
    assert r.json()["amount"] == pytest.approx(8_000_000)


# ── PUT /{allocation_id} ──────────────────────────────────────────────────────


def test_update_allocation(client, db_session, food_cat):
    alloc = _alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    r = client.put(f"/api/budget/{alloc.id}", json={"amount": 7_000_000})
    assert r.status_code == 200
    assert r.json()["amount"] == pytest.approx(7_000_000)


def test_update_nonexistent_allocation_returns_404(client):
    r = client.put("/api/budget/999999", json={"amount": 1_000})
    assert r.status_code == 404


# ── GET /{year_month}/monthly-income ─────────────────────────────────────────


def test_get_monthly_income_empty(client):
    r = client.get("/api/budget/2026-05/monthly-income")
    assert r.status_code == 200
    assert r.json()["income"] == pytest.approx(0)


def test_get_monthly_income_with_data(client, db_session):
    inc_cat = Category(name="IncBE", type=TransactionType.INCOME, color="#10B981", icon="money")
    db_session.add(inc_cat)
    db_session.commit()
    db_session.add(
        Transaction(
            date=date(2026, 5, 15),
            amount=10_000_000,
            type=TransactionType.INCOME,
            category_id=inc_cat.id,
        )
    )
    db_session.commit()
    r = client.get("/api/budget/2026-05/monthly-income")
    assert r.status_code == 200
    assert r.json()["income"] == pytest.approx(10_000_000)


# ── DELETE /category/{category_id} ───────────────────────────────────────────


def test_delete_category_budget(client, db_session, food_cat):
    _alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    _alloc(db_session, food_cat.id, "2026-06", 5_000_000)
    r = client.delete(f"/api/budget/category/{food_cat.id}")
    assert r.status_code == 200
    assert client.get("/api/budget/allocations/2026-05").json() == []


def test_delete_category_budget_no_allocations_returns_404(client, food_cat):
    r = client.delete(f"/api/budget/category/{food_cat.id}")
    assert r.status_code == 404


# ── DELETE /{allocation_id} ───────────────────────────────────────────────────


def test_delete_allocation(client, db_session, food_cat):
    alloc = _alloc(db_session, food_cat.id, "2026-05", 5_000_000)
    r = client.delete(f"/api/budget/{alloc.id}")
    assert r.status_code == 200
    assert client.get("/api/budget/allocations/2026-05").json() == []


def test_delete_nonexistent_allocation_returns_404(client):
    r = client.delete("/api/budget/999999")
    assert r.status_code == 404


def test_budget_rows_spans_year_boundary(client, db_session, food_cat):
    """Allocation in Dec queried for Jan should accumulate across the year boundary."""
    _alloc(db_session, food_cat.id, "2025-12", 5_000_000)
    r = client.get("/api/budget/2026-01/rows")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    # Dec + Jan = 2 months × 5M = 10M cumulative
    assert rows[0]["cumulative_allocated"] == pytest.approx(10_000_000)


def test_budget_rows_december_year_rollover(client, db_session, food_cat):
    """year_month=2026-12 exercises the _m>12 year rollover branch in compute_budget_rows."""
    _alloc(db_session, food_cat.id, "2026-01", 5_000_000)
    r = client.get("/api/budget/2026-12/rows")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    # The allocation spans all 12 months (no subsequent allocation to truncate it)
    assert rows[0]["cumulative_allocated"] == pytest.approx(60_000_000)


def test_budget_rows_deleted_category_skipped(client, db_session, food_cat):
    """Allocation for a hard-deleted category does not crash budget rows."""
    cat_id = food_cat.id
    _alloc(db_session, cat_id, "2026-06", 2_000_000)
    # Hard-delete the category
    db_session.delete(food_cat)
    db_session.commit()
    r = client.get("/api/budget/2026-06/rows")
    assert r.status_code == 200
    rows = r.json()
    # The deleted category's allocation should be skipped
    assert all(row["category_id"] != cat_id for row in rows)
