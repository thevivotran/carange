"""Targeted tests to close coverage gaps across notes, transactions, savings schemas,
templates, import_jobs, budget, project payments, and CSV import."""

import io
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def income_cat(client):
    return client.post(
        "/api/categories/", json={"name": "Salary", "type": "income", "color": "#10B981", "icon": "money"}
    ).json()


@pytest.fixture()
def expense_cat(client):
    return client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#EF4444", "icon": "utensils"}
    ).json()


# ── notes.py: update nonexistent note → 404 ──────────────────────────────────


def test_update_nonexistent_note_returns_404(client):
    r = client.put("/api/notes/999999", json={"title": "Ghost"})
    assert r.status_code == 404


# ── transactions.py: hard_delete paths ───────────────────────────────────────


def test_hard_delete_transaction_not_in_trash_returns_404(client, income_cat):
    """hard-delete on a live (non-trashed) transaction must 404."""
    tx_id = client.post(
        "/api/transactions/",
        json={
            "date": "2026-04-01",
            "amount": 1_000_000,
            "type": "income",
            "category_id": income_cat["id"],
            "payment_method": "cash",
        },
    ).json()["id"]
    r = client.delete(f"/api/transactions/{tx_id}/hard")
    assert r.status_code == 404


def test_hard_delete_nonexistent_transaction_returns_404(client):
    r = client.delete("/api/transactions/999999/hard")
    assert r.status_code == 404


# ── SavingsBundleBase schema validators ───────────────────────────────────────


def _bundle(**overrides):
    base = {
        "name": "Bundle",
        "bank_name": "VCB",
        "type": "fixed_deposit",
        "initial_deposit": 50_000_000,
        "future_amount": 53_000_000,
        "start_date": "2026-01-01",
        "maturity_date": "2027-01-01",
    }
    base.update(overrides)
    return base


def test_create_bundle_zero_future_amount_rejected(client):
    r = client.post("/api/savings/", json=_bundle(future_amount=0))
    assert r.status_code == 422


def test_create_bundle_negative_future_amount_rejected(client):
    r = client.post("/api/savings/", json=_bundle(future_amount=-1))
    assert r.status_code == 422


def test_create_bundle_negative_current_amount_rejected(client):
    r = client.post("/api/savings/", json=_bundle(current_amount=-1))
    assert r.status_code == 422


def test_create_bundle_maturity_before_start_rejected(client):
    r = client.post("/api/savings/", json=_bundle(start_date="2026-06-01", maturity_date="2026-01-01"))
    assert r.status_code == 422


def test_create_bundle_maturity_same_as_start_is_allowed(client):
    # Validator only rejects maturity < start, not equal
    r = client.post("/api/savings/", json=_bundle(start_date="2026-01-01", maturity_date="2026-01-01"))
    assert r.status_code == 200


def test_create_bundle_zero_initial_deposit_rejected(client):
    r = client.post("/api/savings/", json=_bundle(initial_deposit=0))
    assert r.status_code == 422


# ── SavingsBundleUpdate schema validators ────────────────────────────────────


def test_update_bundle_zero_future_amount_rejected(client):
    bundle_id = client.post("/api/savings/", json=_bundle()).json()["id"]
    r = client.put(f"/api/savings/{bundle_id}", json={"future_amount": 0})
    assert r.status_code == 422


def test_update_bundle_negative_initial_deposit_rejected(client):
    bundle_id = client.post("/api/savings/", json=_bundle()).json()["id"]
    r = client.put(f"/api/savings/{bundle_id}", json={"initial_deposit": -1})
    assert r.status_code == 422


def test_update_bundle_maturity_before_start_rejected(client):
    bundle_id = client.post("/api/savings/", json=_bundle()).json()["id"]
    r = client.put(f"/api/savings/{bundle_id}", json={"start_date": "2026-06-01", "maturity_date": "2026-01-01"})
    assert r.status_code == 422


# ── Templates: amount validator ───────────────────────────────────────────────


def test_create_template_zero_amount_rejected(client, expense_cat):
    r = client.post(
        "/api/templates/",
        json={
            "name": "Bad Template",
            "amount": 0,
            "type": "expense",
            "category_id": expense_cat["id"],
            "payment_method": "cash",
        },
    )
    assert r.status_code == 422


def test_create_template_negative_amount_rejected(client, expense_cat):
    r = client.post(
        "/api/templates/",
        json={
            "name": "Bad Template",
            "amount": -500,
            "type": "expense",
            "category_id": expense_cat["id"],
            "payment_method": "cash",
        },
    )
    assert r.status_code == 422


# ── Import jobs: file size limit ──────────────────────────────────────────────


def test_upload_oversized_file_returns_413(client):
    big = b"\x00" * (20 * 1024 * 1024 + 1)  # 20 MB + 1 byte
    r = client.post(
        "/api/import/jobs",
        files=[("files", ("big.png", io.BytesIO(big), "image/png"))],
    )
    assert r.status_code == 413


# ── Budget: no-allocation branch (cumulative_alloc_map = {}) ─────────────────


def test_budget_rows_with_no_prior_allocations(client, expense_cat):
    """GET budget rows for a month with a category allocation but no prior months — hits the else branch."""
    client.post(
        "/api/budget/",
        json={"year_month": "2026-03", "category_id": expense_cat["id"], "amount": 1_000_000},
    )
    r = client.get("/api/budget/2026-03/rows")
    assert r.status_code == 200
    assert any(row["category_id"] == expense_cat["id"] for row in r.json())


def test_budget_rows_deleted_category_skipped(client, expense_cat):
    """Allocation for a category that is subsequently deleted must not crash the rows endpoint."""
    client.post(
        "/api/budget/",
        json={"year_month": "2026-04", "category_id": expense_cat["id"], "amount": 500_000},
    )
    client.delete(f"/api/categories/{expense_cat['id']}")
    r = client.get("/api/budget/2026-04/rows")
    assert r.status_code == 200


# ── Projects: create_payment on nonexistent project → 404 ────────────────────


def test_create_payment_nonexistent_project_returns_404(client):
    r = client.post(
        "/api/projects/999999/payments",
        json={"amount": 500_000, "due_date": "2026-05-01", "status": "pending"},
    )
    assert r.status_code == 404


# ── Projects: update_payment on nonexistent project → 404 ────────────────────


def test_update_payment_nonexistent_project_returns_404(client):
    r = client.patch(
        "/api/projects/999999/payments/1",
        json={"amount": 600_000},
    )
    assert r.status_code == 404


def test_update_payment_nonexistent_payment_returns_404(client):
    project_id = client.post(
        "/api/projects/", json={"name": "Test Project", "type": "real_estate", "priority": "high"}
    ).json()["id"]
    r = client.patch(f"/api/projects/{project_id}/payments/999999", json={"amount": 600_000})
    assert r.status_code == 404


def test_update_payment_amount_only_hits_non_paid_path(client):
    """PATCH with just amount (no status change) exercises the flush/commit return path."""
    project_id = client.post(
        "/api/projects/", json={"name": "P", "type": "real_estate", "priority": "low"}
    ).json()["id"]
    payment_id = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 1_000_000, "due_date": "2026-06-01"},
    ).json()["id"]

    r = client.patch(f"/api/projects/{project_id}/payments/{payment_id}", json={"amount": 2_000_000})
    assert r.status_code == 200
    assert r.json()["amount"] == pytest.approx(2_000_000)
    assert r.json()["status"] == "pending"


# ── CSV import: Vietnamese row-level error and zero-amount skip ───────────────


def _vn_csv(rows: str) -> bytes:
    header = "Năm,Tháng,Loại,Thu,Chi,Ghi chú\n"
    return (header + rows).encode("utf-8-sig")


def _upload_csv(client, content: bytes) -> dict:
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", io.BytesIO(content), "text/csv")},
    )
    assert r.status_code == 200
    return r.json()


def test_vn_csv_invalid_month_goes_to_error_handler(client):
    """Month=13 makes date() throw → hits the generic except branch."""
    csv = _vn_csv("2026,13,Salary,1000000,0,\n")
    result = _upload_csv(client, csv)
    assert result["stats"]["skipped"] >= 1
    assert any("Row 2" in e for e in result["stats"]["errors"])


def test_vn_csv_zero_amounts_row_skipped(client):
    """A valid row where both Thu and Chi are 0 increments skipped, not imported."""
    csv = _vn_csv("2026,3,Salary,0,0,\n")
    result = _upload_csv(client, csv)
    assert result["stats"]["skipped"] >= 1
    assert result["stats"]["income"] == 0
    assert result["stats"]["expense"] == 0


def test_vn_csv_creates_new_category_on_unknown_name(client):
    """Importing a row with a brand-new category name exercises get_or_create_category."""
    csv = _vn_csv("2026,3,BrandNewCategory,500000,0,\n")
    result = _upload_csv(client, csv)
    assert result["stats"]["income"] == 1


# ── CSV import: English row-level generic exception ──────────────────────────


def _en_csv(rows: str) -> bytes:
    header = "date,amount,type,category,description\n"
    return (header + rows).encode("utf-8")


def test_en_csv_invalid_date_skipped_with_error(client):
    """Bad date hits the date-parse error branch in parse_csv_english."""
    csv = _en_csv("not-a-date,1000000,income,Salary,\n")
    result = _upload_csv(client, csv)
    assert result["stats"]["skipped"] >= 1
    assert any("Invalid date" in e for e in result["stats"]["errors"])


def test_en_csv_creates_new_category_on_unknown_name(client):
    """New category name in English CSV exercises get_or_create_category."""
    csv = _en_csv("2026-03-01,750000,income,FreshCategory,bonus\n")
    result = _upload_csv(client, csv)
    assert result["stats"]["income"] == 1
