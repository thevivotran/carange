"""
Sprint 2 data-integrity tests.

Covers:
  - Audit log: PUT writes change rows, GET /history returns them
  - Audit log: no entries when nothing changes
  - Duplicate detection: POST without force returns duplicate_warning
  - Duplicate detection: POST with force=true creates despite match
  - Duplicate detection: ±1-day window (same day, adjacent days, 2+ days apart)
  - Auto-transaction review gate: project payment tx defaults needs_review=True
  - Auto-transaction review gate: savings maturity tx defaults needs_review=True
  - Import job summary endpoint: counts active/auto-approved/needs_review/rejected
  - OCR threshold: env-var driven (tested via processor constant)
"""

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def cats(client):
    inc = client.post(
        "/api/categories/", json={"name": "Salary", "type": "income", "color": "#10B981", "icon": "money"}
    ).json()["id"]
    exp = client.post(
        "/api/categories/", json={"name": "Food", "type": "expense", "color": "#EF4444", "icon": "utensils"}
    ).json()["id"]
    return {"income": inc, "expense": exp}


def _tx(client, *, cats, date="2026-05-01", amount=500_000, type_="expense", force=False, **kw):
    url = "/api/transactions/?force=true" if force else "/api/transactions/"
    payload = {
        "date": date,
        "amount": amount,
        "type": type_,
        "category_id": cats[type_] if isinstance(cats, dict) else cats,
        "payment_method": "cash",
    }
    payload.update(kw)
    r = client.post(url, json=payload)
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Group 1 — Audit log: PUT handler diffing
# ─────────────────────────────────────────────────────────────────────────────


def test_put_creates_audit_entries(client, cats):
    tx = _tx(client, cats=cats, force=True).json()
    client.put(f"/api/transactions/{tx['id']}", json={"amount": 999_000, "description": "Changed"})
    logs = client.get(f"/api/transactions/{tx['id']}/history").json()
    fields = {log["field_name"] for log in logs}
    assert "amount" in fields
    assert "description" in fields


def test_audit_records_old_and_new_values(client, cats):
    tx = _tx(client, cats=cats, amount=100_000, force=True).json()
    client.put(f"/api/transactions/{tx['id']}", json={"amount": 200_000})
    logs = client.get(f"/api/transactions/{tx['id']}/history").json()
    amount_log = next(log for log in logs if log["field_name"] == "amount")
    assert float(amount_log["old_value"]) == 100_000
    assert float(amount_log["new_value"]) == 200_000


def test_no_audit_when_nothing_changes(client, cats):
    tx = _tx(client, cats=cats, force=True).json()
    # Update with a field value identical to current
    client.put(f"/api/transactions/{tx['id']}", json={"payment_method": "cash"})
    logs = client.get(f"/api/transactions/{tx['id']}/history").json()
    assert not any(log["field_name"] == "payment_method" for log in logs)


def test_multiple_fields_logged_in_single_put(client, cats):
    tx = _tx(client, cats=cats, force=True).json()
    client.put(
        f"/api/transactions/{tx['id']}",
        json={"amount": 777_000, "description": "Bulk edit", "payment_method": "bank_transfer"},
    )
    logs = client.get(f"/api/transactions/{tx['id']}/history").json()
    fields = {log["field_name"] for log in logs}
    assert {"amount", "description", "payment_method"}.issubset(fields)


def test_history_returns_404_for_nonexistent_transaction(client):
    r = client.get("/api/transactions/999999/history")
    assert r.status_code == 404


def test_approve_action_logged_as_needs_review_change(client, cats):
    tx = _tx(client, cats=cats, force=True).json()
    client.put(f"/api/transactions/{tx['id']}", json={"needs_review": True})
    client.put(f"/api/transactions/{tx['id']}", json={"needs_review": False})
    logs = client.get(f"/api/transactions/{tx['id']}/history").json()
    nr_logs = [log for log in logs if log["field_name"] == "needs_review"]
    assert len(nr_logs) >= 1
    cleared = next((log for log in nr_logs if log["new_value"] == "False"), None)
    assert cleared is not None


def test_history_ordered_newest_first(client, cats):
    tx = _tx(client, cats=cats, force=True).json()
    client.put(f"/api/transactions/{tx['id']}", json={"description": "First edit"})
    client.put(f"/api/transactions/{tx['id']}", json={"description": "Second edit"})
    logs = client.get(f"/api/transactions/{tx['id']}/history").json()
    desc_logs = [log for log in logs if log["field_name"] == "description"]
    assert len(desc_logs) >= 2
    assert desc_logs[0]["new_value"] == "Second edit"


def test_audit_logs_deleted_with_transaction(client, cats):
    tx = _tx(client, cats=cats, force=True).json()
    client.put(f"/api/transactions/{tx['id']}", json={"amount": 123_000})
    # Soft-delete then hard-delete
    client.delete(f"/api/transactions/{tx['id']}")
    client.delete(f"/api/transactions/{tx['id']}/hard")
    # History endpoint should 404 now
    r = client.get(f"/api/transactions/{tx['id']}/history")
    assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# Group 2 — Duplicate detection on POST
# ─────────────────────────────────────────────────────────────────────────────


def test_duplicate_warning_returned_for_same_day_match(client, cats):
    _tx(client, cats=cats, amount=50_000, date="2026-05-10", force=True)
    r = _tx(client, cats=cats, amount=50_000, date="2026-05-10")
    data = r.json()
    assert r.status_code == 200
    assert data.get("duplicate_warning") is True
    assert len(data["matches"]) >= 1


def test_duplicate_warning_within_one_day_window(client, cats):
    _tx(client, cats=cats, amount=80_000, date="2026-05-10", force=True)
    r = _tx(client, cats=cats, amount=80_000, date="2026-05-11")
    assert r.json().get("duplicate_warning") is True


def test_no_duplicate_warning_two_days_apart(client, cats):
    _tx(client, cats=cats, amount=70_000, date="2026-05-10", force=True)
    r = _tx(client, cats=cats, amount=70_000, date="2026-05-12")
    data = r.json()
    assert data.get("duplicate_warning") is not True
    assert "id" in data


def test_no_duplicate_warning_different_amount(client, cats):
    _tx(client, cats=cats, amount=50_000, date="2026-05-10", force=True)
    r = _tx(client, cats=cats, amount=60_000, date="2026-05-10")
    data = r.json()
    assert data.get("duplicate_warning") is not True
    assert "id" in data


def test_no_duplicate_warning_different_category(client, cats):
    inc2 = client.post(
        "/api/categories/", json={"name": "Bonus", "type": "income", "color": "#34D399", "icon": "gift"}
    ).json()["id"]
    _tx(client, cats=cats, amount=50_000, type_="income", force=True)
    # Different category (inc2 vs cats["income"]) same amount same date
    r = client.post(
        "/api/transactions/",
        json={
            "date": "2026-05-01",
            "amount": 50_000,
            "type": "income",
            "category_id": inc2,
            "payment_method": "cash",
        },
    )
    data = r.json()
    assert data.get("duplicate_warning") is not True
    assert "id" in data


def test_force_true_creates_despite_duplicate(client, cats):
    _tx(client, cats=cats, amount=50_000, date="2026-05-10", force=True)
    r = _tx(client, cats=cats, amount=50_000, date="2026-05-10", force=True)
    assert r.status_code == 200
    assert "id" in r.json()


def test_duplicate_matches_list_contains_matching_tx(client, cats):
    orig = _tx(client, cats=cats, amount=55_000, date="2026-05-15", description="Original", force=True).json()
    r = _tx(client, cats=cats, amount=55_000, date="2026-05-15")
    matches = r.json()["matches"]
    assert any(m["id"] == orig["id"] for m in matches)


def test_deleted_tx_does_not_trigger_duplicate_warning(client, cats):
    """A soft-deleted transaction should not block re-creation."""
    orig = _tx(client, cats=cats, amount=45_000, date="2026-05-20", force=True).json()
    client.delete(f"/api/transactions/{orig['id']}")
    r = _tx(client, cats=cats, amount=45_000, date="2026-05-20")
    data = r.json()
    assert data.get("duplicate_warning") is not True
    assert "id" in data


# ─────────────────────────────────────────────────────────────────────────────
# Group 3 — Auto-transaction review gate
# ─────────────────────────────────────────────────────────────────────────────


def test_project_payment_auto_tx_needs_review(client, cats):
    project = client.post(
        "/api/projects/",
        json={"name": "Home", "type": "real_estate"},
    ).json()
    payment = client.post(
        f"/api/projects/{project['id']}/payments",
        json={"amount": 1_000_000, "due_date": "2026-05-01"},
    ).json()
    # Mark paid — auto-creates transaction
    client.patch(
        f"/api/projects/{project['id']}/payments/{payment['id']}",
        json={"status": "paid", "category_id": cats["expense"]},
    )
    txs = client.get(f"/api/transactions/?source=project_payment&project_id={project['id']}").json()
    assert len(txs) == 1
    assert txs[0]["needs_review"] is True


def test_savings_maturity_auto_tx_needs_review(client, cats):
    bundle = client.post(
        "/api/savings/",
        json={
            "name": "Test FD",
            "bank_name": "TestBank",
            "type": "fixed_deposit",
            "initial_deposit": 10_000_000,
            "future_amount": 10_500_000,
            "interest_rate": 5.0,
            "start_date": "2026-01-01",
            "maturity_date": "2026-12-31",
        },
    ).json()
    client.post(f"/api/savings/{bundle['id']}/mark-completed")
    txs = client.get("/api/transactions/?source=savings_maturity").json()
    assert len(txs) >= 1
    assert txs[0]["needs_review"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Group 4 — Import job summary endpoint
# ─────────────────────────────────────────────────────────────────────────────


def test_import_job_summary_404_unknown_job(client):
    r = client.get("/api/import/jobs/999999/summary")
    assert r.status_code == 404


def test_import_job_summary_empty_job(client, tmp_path):
    """A job with no transactions → all counts zero."""
    import hashlib
    import os

    img = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    digest = hashlib.sha256(img).hexdigest()
    upload_dir = os.getenv("UPLOAD_DIR", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    fpath = os.path.join(upload_dir, digest + ".jpg")
    with open(fpath, "wb") as f:
        f.write(img)

    from io import BytesIO

    r = client.post(
        "/api/import/jobs",
        files=[("files", ("test.jpg", BytesIO(img), "image/jpeg"))],
    )
    job = r.json()[0]
    s = client.get(f"/api/import/jobs/{job['id']}/summary").json()
    assert s["total"] == 0
    assert s["auto_approved"] == 0
    assert s["needs_review"] == 0
    assert s["rejected"] == 0


def test_import_job_summary_counts_correctly(client, cats, db_session):
    """Create transactions linked to a job manually and verify counts."""
    from datetime import date, datetime, timezone
    from app.models.database import ImportJob, ImportJobStatus, Transaction, TransactionType

    job = ImportJob(
        filename="test.jpg",
        file_path="test.jpg",
        image_hash="abcd1234" + "0" * 56,
        status=ImportJobStatus.DONE,
    )
    db_session.add(job)
    db_session.flush()

    tx_date = date(2026, 5, 1)
    tx1 = Transaction(
        date=tx_date,
        amount=100_000,
        type=TransactionType.EXPENSE,
        category_id=cats["expense"],
        payment_method="cash",
        source="timo",
        import_job_id=job.id,
        confidence_score=0.98,
        needs_review=False,
    )
    tx2 = Transaction(
        date=tx_date,
        amount=200_000,
        type=TransactionType.EXPENSE,
        category_id=cats["expense"],
        payment_method="cash",
        source="timo",
        import_job_id=job.id,
        confidence_score=0.60,
        needs_review=True,
    )
    tx3 = Transaction(
        date=tx_date,
        amount=300_000,
        type=TransactionType.EXPENSE,
        category_id=cats["expense"],
        payment_method="cash",
        source="timo",
        import_job_id=job.id,
        confidence_score=0.40,
        needs_review=True,
        deleted_at=datetime.now(timezone.utc),
    )
    db_session.add_all([tx1, tx2, tx3])
    db_session.commit()

    s = client.get(f"/api/import/jobs/{job.id}/summary").json()
    assert s["total"] == 2  # active only
    assert s["auto_approved"] == 1
    assert s["needs_review"] == 1
    assert s["rejected"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Group 5 — OCR threshold is configurable
# ─────────────────────────────────────────────────────────────────────────────


def test_ocr_review_threshold_default_is_0_95():
    """REVIEW_THRESHOLD default must be 0.95 so high-confidence OCR results auto-approve."""
    from ocr_worker.processor import REVIEW_THRESHOLD

    assert REVIEW_THRESHOLD == 0.95
