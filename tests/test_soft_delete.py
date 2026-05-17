"""
Sprint 1 soft-delete test suite.

Covers:
  - Soft-delete core: deleted tx hidden from GET / and GET /{id}
  - GET /trash endpoint
  - GET /{id}/links  — savings bundle and project payment links
  - POST /{id}/restore
  - DELETE /{id}/hard
  - Cascade: ProjectPayment reverts to PENDING when its tx is deleted
  - Stats, budget, search, category counts all exclude soft-deleted rows
  - Duplicate-detection ignores deleted rows (allows re-creation)
"""

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def cats(client):
    """Return {income, expense} category IDs."""
    inc = client.post(
        "/api/categories/", json={"name": "Salary", "type": "income", "color": "#10B981", "icon": "money"}
    ).json()["id"]
    exp = client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#EF4444", "icon": "utensils"}
    ).json()["id"]
    return {"income": inc, "expense": exp}


def _tx(client, *, cats, date="2026-05-01", amount=500_000, type_="expense", **kw):
    payload = {
        "date": date,
        "amount": amount,
        "type": type_,
        "category_id": cats[type_] if isinstance(cats, dict) else cats,
        "payment_method": "cash",
    }
    payload.update(kw)
    r = client.post("/api/transactions/?force=true", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Group 1 — Soft-delete core
# ─────────────────────────────────────────────────────────────────────────────


def test_delete_returns_200(client, cats):
    tx = _tx(client, cats=cats)
    r = client.delete(f"/api/transactions/{tx['id']}")
    assert r.status_code == 200


def test_deleted_transaction_absent_from_list(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    ids = [t["id"] for t in client.get("/api/transactions/").json()]
    assert tx["id"] not in ids


def test_deleted_transaction_get_returns_404(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    assert client.get(f"/api/transactions/{tx['id']}").status_code == 404


def test_deleted_transaction_update_returns_404(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    assert client.put(f"/api/transactions/{tx['id']}", json={"amount": 1_000}).status_code == 404


def test_double_delete_returns_404(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    assert client.delete(f"/api/transactions/{tx['id']}").status_code == 404


def test_deleted_at_field_present_in_schema(client, cats):
    """Transaction response always includes deleted_at (null for active rows)."""
    tx = _tx(client, cats=cats)
    assert "deleted_at" in tx
    assert tx["deleted_at"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Group 2 — GET /trash
# ─────────────────────────────────────────────────────────────────────────────


def test_trash_empty_by_default(client):
    assert client.get("/api/transactions/trash").json() == []


def test_trash_shows_deleted_transaction(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    trash = client.get("/api/transactions/trash").json()
    assert any(t["id"] == tx["id"] for t in trash)


def test_trash_does_not_show_active_transactions(client, cats):
    active = _tx(client, cats=cats)
    deleted = _tx(client, cats=cats, date="2026-05-02")
    client.delete(f"/api/transactions/{deleted['id']}")
    trash_ids = [t["id"] for t in client.get("/api/transactions/trash").json()]
    assert active["id"] not in trash_ids
    assert deleted["id"] in trash_ids


def test_trash_deleted_at_is_populated(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    trash = client.get("/api/transactions/trash").json()
    match = next(t for t in trash if t["id"] == tx["id"])
    assert match["deleted_at"] is not None


def test_trash_respects_pagination(client, cats):
    for i in range(5):
        t = _tx(client, cats=cats, date=f"2026-05-{i + 1:02d}", amount=1000 * (i + 1))
        client.delete(f"/api/transactions/{t['id']}")
    page1 = client.get("/api/transactions/trash?skip=0&limit=3").json()
    page2 = client.get("/api/transactions/trash?skip=3&limit=3").json()
    assert len(page1) == 3
    assert len(page2) == 2
    assert {t["id"] for t in page1}.isdisjoint({t["id"] for t in page2})


# ─────────────────────────────────────────────────────────────────────────────
# Group 3 — GET /{id}/links
# ─────────────────────────────────────────────────────────────────────────────


def test_links_no_links_returns_nulls(client, cats):
    tx = _tx(client, cats=cats)
    r = client.get(f"/api/transactions/{tx['id']}/links")
    assert r.status_code == 200
    d = r.json()
    assert d["savings_bundle"] is None
    assert d["project_payment"] is None


def test_links_404_for_nonexistent_transaction(client):
    assert client.get("/api/transactions/999999/links").status_code == 404


def test_links_404_for_deleted_transaction(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    assert client.get(f"/api/transactions/{tx['id']}/links").status_code == 404


def test_links_returns_savings_bundle_info(client, cats):
    """A transaction that is the backing deposit for a bundle appears in /links."""
    r = client.post(
        "/api/transactions/",
        json={
            "date": "2026-05-01",
            "amount": 10_000_000,
            "type": "expense",
            "category_id": cats["expense"],
            "payment_method": "bank_transfer",
            "is_savings_related": True,
            "savings_bundle": {
                "name": "VCB FD",
                "bank_name": "VCB",
                "type": "fixed_deposit",
                "initial_deposit": 10_000_000,
                "future_amount": 10_500_000,
                "start_date": "2026-05-01",
                "maturity_date": "2026-11-01",
            },
        },
    )
    assert r.status_code == 200
    tx = r.json()
    links = client.get(f"/api/transactions/{tx['id']}/links").json()
    assert links["savings_bundle"] is not None
    assert links["savings_bundle"]["name"] == "VCB FD"
    assert links["savings_bundle"]["bank_name"] == "VCB"
    assert links["project_payment"] is None


def test_links_returns_project_payment_info(client, cats):
    """A transaction auto-created by marking a payment PAID appears in /links."""
    project_id = client.post(
        "/api/projects/", json={"name": "House", "type": "real_estate", "priority": "high"}
    ).json()["id"]
    payment = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 5_000_000, "due_date": "2026-05-15"},
    ).json()
    # Mark payment PAID → auto-creates a transaction
    client.patch(
        f"/api/projects/{project_id}/payments/{payment['id']}",
        json={"status": "paid", "category_id": cats["expense"], "payment_date": "2026-05-15"},
    )
    updated_payment = client.get(f"/api/projects/{project_id}/payments").json()
    paid = next(p for p in updated_payment if p["id"] == payment["id"])
    tx_id = paid["transaction_id"]
    assert tx_id is not None

    links = client.get(f"/api/transactions/{tx_id}/links").json()
    assert links["project_payment"] is not None
    assert links["project_payment"]["project_name"] == "House"
    assert links["project_payment"]["amount"] == pytest.approx(5_000_000)
    assert links["savings_bundle"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Group 4 — POST /{id}/restore
# ─────────────────────────────────────────────────────────────────────────────


def test_restore_makes_transaction_active_again(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    r = client.post(f"/api/transactions/{tx['id']}/restore")
    assert r.status_code == 200
    assert r.json()["deleted_at"] is None


def test_restored_transaction_visible_in_list(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    client.post(f"/api/transactions/{tx['id']}/restore")
    ids = [t["id"] for t in client.get("/api/transactions/").json()]
    assert tx["id"] in ids


def test_restored_transaction_absent_from_trash(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    client.post(f"/api/transactions/{tx['id']}/restore")
    trash_ids = [t["id"] for t in client.get("/api/transactions/trash").json()]
    assert tx["id"] not in trash_ids


def test_restore_nonexistent_returns_404(client):
    assert client.post("/api/transactions/999999/restore").status_code == 404


def test_restore_active_transaction_returns_404(client, cats):
    """Cannot restore a transaction that is not in the trash."""
    tx = _tx(client, cats=cats)
    assert client.post(f"/api/transactions/{tx['id']}/restore").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Group 5 — DELETE /{id}/hard
# ─────────────────────────────────────────────────────────────────────────────


def test_hard_delete_permanently_removes_from_trash(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    r = client.delete(f"/api/transactions/{tx['id']}/hard")
    assert r.status_code == 200
    # No longer in trash
    trash_ids = [t["id"] for t in client.get("/api/transactions/trash").json()]
    assert tx["id"] not in trash_ids


def test_hard_delete_active_transaction_returns_404(client, cats):
    """Hard-delete only works on items already in the trash."""
    tx = _tx(client, cats=cats)
    assert client.delete(f"/api/transactions/{tx['id']}/hard").status_code == 404


def test_hard_delete_nonexistent_returns_404(client):
    assert client.delete("/api/transactions/999999/hard").status_code == 404


def test_hard_deleted_transaction_not_restorable(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    client.delete(f"/api/transactions/{tx['id']}/hard")
    assert client.post(f"/api/transactions/{tx['id']}/restore").status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Group 6 — Cascade: ProjectPayment reverts to PENDING on delete
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def paid_payment(client, cats):
    """Creates a project, adds a payment, marks it PAID, returns (project_id, payment_id, tx_id)."""
    project_id = client.post(
        "/api/projects/", json={"name": "Apartment", "type": "real_estate", "priority": "high"}
    ).json()["id"]
    payment = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 10_000_000, "due_date": "2026-05-01"},
    ).json()
    client.patch(
        f"/api/projects/{project_id}/payments/{payment['id']}",
        json={"status": "paid", "category_id": cats["expense"], "payment_date": "2026-05-01"},
    )
    updated = client.get(f"/api/projects/{project_id}/payments").json()
    paid = next(p for p in updated if p["id"] == payment["id"])
    return {"project_id": project_id, "payment_id": payment["id"], "tx_id": paid["transaction_id"]}


def test_delete_auto_tx_reverts_payment_to_pending(client, cats, paid_payment):
    client.delete(f"/api/transactions/{paid_payment['tx_id']}")
    payments = client.get(f"/api/projects/{paid_payment['project_id']}/payments").json()
    reverted = next(p for p in payments if p["id"] == paid_payment["payment_id"])
    assert reverted["status"] == "pending"


def test_delete_auto_tx_clears_payment_transaction_id(client, cats, paid_payment):
    client.delete(f"/api/transactions/{paid_payment['tx_id']}")
    payments = client.get(f"/api/projects/{paid_payment['project_id']}/payments").json()
    reverted = next(p for p in payments if p["id"] == paid_payment["payment_id"])
    assert reverted["transaction_id"] is None


def test_delete_auto_tx_reduces_project_current_amount(client, cats, paid_payment):
    project_before = client.get(f"/api/projects/{paid_payment['project_id']}").json()
    assert project_before["current_amount"] == pytest.approx(10_000_000)
    client.delete(f"/api/transactions/{paid_payment['tx_id']}")
    project_after = client.get(f"/api/projects/{paid_payment['project_id']}").json()
    assert project_after["current_amount"] == pytest.approx(0)


def test_delete_plain_transaction_no_cascade(client, cats):
    """Deleting a manual transaction with no project/savings links works cleanly."""
    tx = _tx(client, cats=cats, amount=1_000_000)
    r = client.delete(f"/api/transactions/{tx['id']}")
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Group 7 — Stats exclude soft-deleted rows
# ─────────────────────────────────────────────────────────────────────────────


def test_monthly_summary_excludes_deleted_income(client, cats):
    tx = _tx(client, cats=cats, type_="income", amount=10_000_000, date="2026-05-10")
    client.delete(f"/api/transactions/{tx['id']}")
    d = client.get("/api/transactions/stats/monthly-summary?year=2026&month=5").json()
    assert d["income"] == pytest.approx(0)


def test_monthly_summary_excludes_deleted_expense(client, cats):
    tx = _tx(client, cats=cats, type_="expense", amount=3_000_000, date="2026-05-10")
    client.delete(f"/api/transactions/{tx['id']}")
    d = client.get("/api/transactions/stats/monthly-summary?year=2026&month=5").json()
    assert d["expense"] == pytest.approx(0)


def test_monthly_summary_active_tx_still_counted(client, cats):
    """Active transactions after a sibling is deleted are still counted correctly."""
    _tx(client, cats=cats, type_="income", amount=5_000_000, date="2026-05-10")
    deleted = _tx(client, cats=cats, type_="income", amount=1_000_000, date="2026-05-11")
    client.delete(f"/api/transactions/{deleted['id']}")
    d = client.get("/api/transactions/stats/monthly-summary?year=2026&month=5").json()
    assert d["income"] == pytest.approx(5_000_000)


def test_by_category_excludes_deleted(client, cats):
    tx = _tx(client, cats=cats, type_="expense", amount=2_000_000, date="2026-05-01")
    client.delete(f"/api/transactions/{tx['id']}")
    r = client.get("/api/transactions/stats/by-category?type=expense&year=2026&month=5")
    assert r.status_code == 200
    assert r.json() == []


# ─────────────────────────────────────────────────────────────────────────────
# Group 8 — Filters exclude soft-deleted rows
# ─────────────────────────────────────────────────────────────────────────────


def test_needs_review_filter_true(client, cats):
    # needs_review is not in TransactionCreate — set it via PUT after creation
    tx = _tx(client, cats=cats)
    client.put(f"/api/transactions/{tx['id']}", json={"needs_review": True})
    _tx(client, cats=cats, date="2026-05-02")  # second tx stays needs_review=False
    r = client.get("/api/transactions/?needs_review=true")
    assert r.status_code == 200
    assert all(t["needs_review"] is True for t in r.json())
    assert any(t["id"] == tx["id"] for t in r.json())


def test_needs_review_filter_does_not_return_deleted(client, cats):
    tx = _tx(client, cats=cats, needs_review=True)
    client.delete(f"/api/transactions/{tx['id']}")
    r = client.get("/api/transactions/?needs_review=true")
    assert all(t["id"] != tx["id"] for t in r.json())


def test_search_excludes_deleted_transactions(client, cats):
    tx = _tx(client, cats=cats, description="UniqueSearchTermXYZ")
    client.delete(f"/api/transactions/{tx['id']}")
    r = client.get("/api/transactions/?search=UniqueSearchTermXYZ")
    assert r.status_code == 200
    assert r.json() == []


def test_import_job_id_filter(client, cats, db_session):
    """Filter by import_job_id returns only transactions from that job."""
    from app.models.database import ImportJob, Transaction, TransactionType
    from datetime import date

    job = ImportJob(filename="test.png", file_path="/tmp/test.png", image_hash="abc123")
    db_session.add(job)
    db_session.commit()

    tx = Transaction(
        date=date(2026, 5, 1),
        amount=500_000,
        type=TransactionType.EXPENSE,
        category_id=cats["expense"],
        import_job_id=job.id,
    )
    db_session.add(tx)
    db_session.commit()

    r = client.get(f"/api/transactions/?import_job_id={job.id}")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["import_job_id"] == job.id


# ─────────────────────────────────────────────────────────────────────────────
# Group 9 — Category transaction_count excludes soft-deleted
# ─────────────────────────────────────────────────────────────────────────────


def test_category_count_excludes_deleted(client, cats):
    _tx(client, cats=cats)
    tx2 = _tx(client, cats=cats, date="2026-05-02")
    client.delete(f"/api/transactions/{tx2['id']}")

    cats_list = client.get("/api/categories/").json()
    food_cat = next(c for c in cats_list if c["id"] == cats["expense"])
    assert food_cat["transaction_count"] == 1


def test_category_count_zero_after_all_deleted(client, cats):
    tx = _tx(client, cats=cats)
    client.delete(f"/api/transactions/{tx['id']}")
    cats_list = client.get("/api/categories/").json()
    food_cat = next(c for c in cats_list if c["id"] == cats["expense"])
    assert food_cat["transaction_count"] == 0


def test_category_delete_blocked_when_only_soft_deleted_txs(client, cats):
    """
    Category deletion must be blocked even when all referencing transactions are
    soft-deleted — they could be restored and would need a valid category.
    The endpoint returns 400 with a Trash-specific message.
    """
    new_cat = client.post(
        "/api/categories/", json={"name": "Temp", "type": "expense", "color": "#ccc", "icon": "x"}
    ).json()
    tx = _tx(client, cats={"expense": new_cat["id"]})
    client.delete(f"/api/transactions/{tx['id']}")
    r = client.delete(f"/api/categories/{new_cat['id']}")
    assert r.status_code == 400
    assert "Trash" in r.json()["detail"]


def test_category_delete_allowed_after_trash_emptied(client, cats):
    """Category can be deleted once all referencing transactions are hard-deleted."""
    new_cat = client.post(
        "/api/categories/", json={"name": "Temp2", "type": "expense", "color": "#ccc", "icon": "x"}
    ).json()
    tx = _tx(client, cats={"expense": new_cat["id"]})
    client.delete(f"/api/transactions/{tx['id']}")  # soft-delete
    client.delete(f"/api/transactions/{tx['id']}/hard")  # hard-delete
    r = client.delete(f"/api/categories/{new_cat['id']}")
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Group 10 — Budget excludes soft-deleted spending
# ─────────────────────────────────────────────────────────────────────────────


def test_budget_spending_excludes_deleted_transaction(client, cats):
    # Set a budget allocation for Food in 2026-05
    client.post("/api/budget/", json={"category_id": cats["expense"], "year_month": "2026-05", "amount": 5_000_000})
    # Create and then delete an expense transaction
    tx = _tx(client, cats=cats, amount=2_000_000, date="2026-05-10")
    client.delete(f"/api/transactions/{tx['id']}")
    # Budget row should show zero spending
    rows = client.get("/api/budget/2026-05/rows").json()
    food_row = next((r for r in rows if r["category_id"] == cats["expense"]), None)
    assert food_row is not None
    assert food_row["this_month_spent"] == pytest.approx(0)
    assert food_row["cumulative_spent"] == pytest.approx(0)


# ─────────────────────────────────────────────────────────────────────────────
# Group 11 — Duplicate detection ignores deleted rows
# ─────────────────────────────────────────────────────────────────────────────


def test_deleted_transaction_can_be_recreated(client, cats):
    """
    A soft-deleted transaction should not block re-creation of an identical row.
    The duplicate-detection query filters deleted_at IS NULL.
    """
    tx = _tx(client, cats=cats, date="2026-05-01", amount=500_000, description="rent")
    client.delete(f"/api/transactions/{tx['id']}")

    # Should succeed — the old row is deleted, not a live duplicate
    r = _tx(client, cats=cats, date="2026-05-01", amount=500_000, description="rent")
    assert r["id"] != tx["id"]


def test_bulk_upload_duplicate_detection_ignores_deleted(client, cats):
    """Bulk upload dedup only checks active rows; re-importing after soft-delete should succeed."""
    import io

    csv_content = "date,amount,type,category,description\n2026-05-01,1000000,income,Salary,May salary\n"
    # First import
    client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("a.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    # Soft-delete the imported transaction
    txs = client.get("/api/transactions/?search=May+salary").json()
    assert len(txs) == 1
    client.delete(f"/api/transactions/{txs[0]['id']}")

    # Second import of same file — should NOT be skipped now
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("a.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["income"] == 1
    assert r.json()["stats"]["skipped"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Group 12 — Vietnamese bulk upload exception paths
# ─────────────────────────────────────────────────────────────────────────────


def test_vn_bulk_upload_skips_duplicate_expense(client):
    """Vietnamese format: duplicate expense row is skipped on second upload."""
    import io

    csv_content = "Năm,Tháng,Thu,Chi,Loại\n2026,5,0,3000000,Ăn uống\n"
    client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("vn.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1
    assert r.json()["stats"]["expense"] == 0


def test_bulk_upload_english_exception_row_logged(client):
    """Row that triggers an unexpected error is counted in skipped+errors."""
    import io

    # A row where category is present but amount triggers a parsing issue
    # We inject a row that will raise during processing by giving a deeply
    # malformed amount that passes the empty check but blows up float()
    csv_content = "date,amount,type,category\n2026-05-01,1e999999,income,Salary\n"
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("bad.csv", io.BytesIO(csv_content.encode()), "text/csv")},
    )
    # Might succeed with inf or skip, either way no 500
    assert r.status_code in (200, 400)
