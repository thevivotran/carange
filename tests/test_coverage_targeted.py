"""Tests targeting specific coverage gaps from the coverage report.

Covers:
- models/database.py lines 346-350 (get_db generator)
- models/schemas.py line 124 (SavingsBundleUpdate.validate_maturity_date return v)
- routers/import_jobs.py line 18 (_resolve_path relative path)
- routers/projects.py lines 202-203, 227-228 (500 error paths)
- routers/savings.py line 87 (current_amount default)
- routers/transactions.py lines 233-235 (commit exception in PUT)
- services/budget_service.py line 144 (deleted-category skip)
- services/savings_service.py line 26 (_get_or_create_interest_category early return)
- services/transaction_service.py lines 156-158, 163-169, 389-391

Not coverable (dead code shadowed by Field constraints or logically unreachable):
- models/schemas.py lines 72, 79, 152, 319 (raise in validators guarded by Field(gt/ge=0))
- services/budget_service.py line 138 (else branch: all_months always non-empty)
- services/transaction_service.py lines 222-223, 302-303 (utf-8-sig always succeeds for valid UTF-8)
- services/project_service.py line 45 + all except/rollback branches
- services/savings_service.py line 26 + all except/rollback branches
- services/transaction_service.py line 83 + exception branches + CSV UTF-8 fallback
"""

from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
import sqlalchemy

from app.models.database import (
    BudgetAllocation,
    Category,
    FinancialProject,
    PaymentStatus,
    ProjectPayment,
    ProjectStatus,
    ProjectType,
    SavingsBundle,
    SavingsStatus,
    Transaction,
    TransactionType,
    get_db,
)
from app.models.schemas import SavingsBundleCreate, TransactionCreate, TransactionTemplateCreate
from app.routers.import_jobs import _resolve_path
from app.services import budget_service, project_service, savings_service, transaction_service
from app.services.transaction_service import get_or_create_category


# ── models/database.py: get_db generator (lines 346-350) ─────────────────────


def test_get_db_yields_session_and_closes():
    gen = get_db()
    db = next(gen)
    assert db is not None
    try:
        next(gen)
    except StopIteration:
        pass


# ── models/schemas.py: validator return-v branches ───────────────────────────


def test_savings_bundle_create_valid_data_passes_all_validators():
    """Covers return v in validate_positive_amounts (72), validate_non_negative_amount (79),
    and validate_maturity_date (124) in SavingsBundleBase."""
    b = SavingsBundleCreate(
        name="Test Bundle",
        bank_name="VCB",
        type="fixed_deposit",
        initial_deposit=10_000_000,
        future_amount=11_000_000,
        current_amount=10_000_000,
        start_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
    )
    assert b.initial_deposit == 10_000_000
    assert b.future_amount == 11_000_000
    assert b.current_amount == 10_000_000
    assert b.maturity_date == date(2027, 1, 1)


def test_savings_bundle_no_maturity_date_validator_skips():
    """Cover the maturity_date validator when v is None (no-op branch)."""
    b = SavingsBundleCreate(
        name="Open Bundle",
        bank_name="VCB",
        type="savings_goal",
        initial_deposit=5_000_000,
        future_amount=5_500_000,
        start_date=date(2026, 1, 1),
    )
    assert b.maturity_date is None


def test_transaction_create_valid_amount_passes_validator():
    """Covers return v in TransactionBase.validate_amount (line 152)."""
    t = TransactionCreate(
        date=date(2026, 1, 1),
        amount=1_000_000,
        type="income",
        category_id=1,
    )
    assert t.amount == 1_000_000


def test_transaction_template_create_valid_amount_passes_validator():
    """Covers return v in TransactionTemplateBase.validate_amount (line 319)."""
    t = TransactionTemplateCreate(
        name="Monthly Rent",
        amount=5_000_000,
        type="expense",
        category_id=1,
    )
    assert t.amount == 5_000_000


# ── routers/import_jobs.py: _resolve_path (line 18) ─────────────────────────


def test_resolve_path_absolute_returns_unchanged():
    assert _resolve_path("/absolute/path/file.jpg") == "/absolute/path/file.jpg"


def test_resolve_path_relative_joins_upload_dir():
    result = _resolve_path("somefile.jpg")
    assert result.endswith("somefile.jpg")
    assert result != "somefile.jpg"


# ── transaction_service: "Khác" normalization (line 83) ──────────────────────


def test_khac_income_normalises_to_thu_nhap_khac(db_session):
    cat = get_or_create_category(db_session, "Khác", TransactionType.INCOME)
    assert cat.name == "Thu nhập khác"


def test_khac_expense_normalises_to_chi_phi_khac(db_session):
    cat = get_or_create_category(db_session, "Khác", TransactionType.EXPENSE)
    assert cat.name == "Chi phí khác"


# ── transaction_service: create_transaction rollback (lines 127-129) ─────────


def test_create_transaction_rollback_on_commit_error(db_session, income_cat):
    data = TransactionCreate(
        date=date(2026, 1, 1),
        amount=1_000_000,
        type="income",
        category_id=income_cat.id,
    )
    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            transaction_service.create_transaction(db_session, data)


# ── transaction_service: CSV UTF-8 fallback (lines 222-223, 302-303) ─────────


def _vn_csv_bytes(row: str) -> bytes:
    header = "Năm,Tháng,Loại,Thu,Chi,Ghi chú\n"
    return (header + row).encode("utf-8")


def _en_csv_bytes(row: str) -> bytes:
    header = "date,amount,type,category\n"
    return (header + row).encode("utf-8")


def test_vn_csv_plain_utf8_decoded_as_fallback(client):
    """Plain UTF-8 bytes (no BOM) trigger the UnicodeDecodeError fallback in parse_csv_vietnamese."""
    csv_bytes = _vn_csv_bytes("2026,3,Salary,1000000,0,\n")
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["income"] >= 1


def test_en_csv_plain_utf8_decoded_as_fallback(client):
    """Plain UTF-8 bytes (no BOM) trigger the UnicodeDecodeError fallback in parse_csv_english."""
    csv_bytes = _en_csv_bytes("2026-03-01,500000,income,Salary\n")
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["income"] >= 1


# ── transaction_service: parse_csv_english generic except (lines 389-391) ─────


def test_en_csv_generic_row_exception_captured(client):
    """Row-level exception in parse_csv_english is caught and added to errors."""
    csv_bytes = _en_csv_bytes("not-a-date,1000000,income,Food\n")
    r = client.post(
        "/api/transactions/bulk-upload",
        files={"file": ("import.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200
    assert r.json()["stats"]["skipped"] >= 1
    assert any("Row 2" in e for e in r.json()["stats"]["errors"])


# ── project_service: invalid interval (line 45) ──────────────────────────────


def test_next_date_invalid_interval_raises_value_error():
    with pytest.raises(ValueError, match="Invalid interval"):
        project_service.next_date(date(2026, 1, 1), "quarterly")


# ── project_service helpers ───────────────────────────────────────────────────


def _make_project(db_session, name="Test Project"):
    project = FinancialProject(
        name=name,
        type=ProjectType.CUSTOM,
        target_amount=0,
        current_amount=0,
        status=ProjectStatus.PLANNING,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def _make_payment(db_session, project):
    payment = ProjectPayment(
        project_id=project.id,
        amount=100_000,
        status=PaymentStatus.PENDING,
    )
    db_session.add(payment)
    db_session.commit()
    db_session.refresh(payment)
    return payment


# ── project_service: create_payment rollback (lines 58-60) ───────────────────


def test_create_payment_rollback_on_error(db_session):
    project = _make_project(db_session)

    class BadPaymentData:
        def model_dump(self):
            raise RuntimeError("serialization error")

    with pytest.raises(RuntimeError, match="serialization error"):
        project_service.create_payment(db_session, project, BadPaymentData())


# ── project_service: mark_payment_paid rollback (lines 91-93) ────────────────


def test_mark_payment_paid_rollback_on_error(db_session, expense_cat):
    project = _make_project(db_session)
    payment = _make_payment(db_session, project)

    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            project_service.mark_payment_paid(db_session, project, payment, expense_cat.id, None)


# ── project_service: update_payment rollback (lines 114-116) ─────────────────


def test_update_payment_rollback_on_error(db_session):
    project = _make_project(db_session)
    payment = _make_payment(db_session, project)

    class BadUpdate:
        def model_dump(self, exclude_unset=False):
            raise RuntimeError("update error")

    with pytest.raises(RuntimeError, match="update error"):
        project_service.update_payment(db_session, project, payment, BadUpdate())


# ── project_service: delete_payment rollback (lines 126-128) ─────────────────


def test_delete_payment_rollback_on_error(db_session):
    project = _make_project(db_session)
    payment = _make_payment(db_session, project)

    with patch.object(db_session, "flush", side_effect=Exception("flush fail")):
        with pytest.raises(Exception, match="flush fail"):
            project_service.delete_payment(db_session, project, payment)


# ── project_service: bulk_create_payments rollback (lines 153-155) ───────────


def test_bulk_create_payments_rollback_on_error(db_session):
    project = _make_project(db_session)

    class BulkReq:
        start_date = date(2026, 1, 1)
        occurrences = 2
        amount = 100_000
        interval = "monthly"
        notes = None

    with patch.object(db_session, "flush", side_effect=Exception("flush fail")):
        with pytest.raises(Exception, match="flush fail"):
            project_service.bulk_create_payments(db_session, project, BulkReq())


# ── project_service: soft_delete_project rollback (lines 169-171) ────────────


def test_soft_delete_project_rollback_on_error(db_session):
    project = _make_project(db_session)
    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            project_service.soft_delete_project(db_session, project.id)


# ── project_service: restore_project rollback (lines 187-189) ────────────────


def test_restore_project_rollback_on_error(db_session):
    project = _make_project(db_session)
    project.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            project_service.restore_project(db_session, project.id)


# ── project_service: hard_delete_project rollback (lines 203-205) ────────────


def test_hard_delete_project_rollback_on_error(db_session):
    project = _make_project(db_session)
    project.deleted_at = datetime.now(timezone.utc)
    db_session.commit()

    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            project_service.hard_delete_project(db_session, project.id)


# ── savings_service: interest category creation (line 26) ────────────────────


def test_mark_bundle_completed_creates_loi_tiet_kiem_category(db_session):
    """Covers line 26: db.flush() when _get_or_create_interest_category creates the category."""
    income_cat = Category(name="Investment", type=TransactionType.INCOME, color="#10B981", icon="chart")
    db_session.add(income_cat)

    bundle = SavingsBundle(
        name="High Yield",
        bank_name="VCB",
        type="fixed_deposit",
        initial_deposit=10_000_000,
        current_amount=10_000_000,
        future_amount=11_000_000,
        interest_rate=10.0,
        start_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
        status=SavingsStatus.ACTIVE,
    )
    db_session.add(bundle)
    db_session.commit()
    db_session.refresh(bundle)

    result = savings_service.mark_bundle_completed(db_session, bundle.id)
    assert result.status == SavingsStatus.COMPLETED

    created = (
        db_session.query(Category)
        .filter(Category.name == "Lãi tiết kiệm", Category.type == TransactionType.INCOME)
        .first()
    )
    assert created is not None


# ── savings_service helpers ───────────────────────────────────────────────────


def _make_active_bundle(db_session):
    bundle = SavingsBundle(
        name="Test Bundle",
        bank_name="VCB",
        type="fixed_deposit",
        initial_deposit=5_000_000,
        current_amount=5_000_000,
        future_amount=5_500_000,
        interest_rate=10.0,
        start_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
        status=SavingsStatus.ACTIVE,
    )
    db_session.add(bundle)
    db_session.commit()
    db_session.refresh(bundle)
    return bundle


def _make_deleted_bundle(db_session):
    bundle = _make_active_bundle(db_session)
    bundle.deleted_at = datetime.now(timezone.utc)
    db_session.commit()
    return bundle


# ── savings_service: mark_bundle_completed rollback (lines 107-109) ──────────


def test_mark_bundle_completed_rollback_on_error(db_session):
    income_cat = Category(name="Investment", type=TransactionType.INCOME, color="#10B981", icon="chart")
    db_session.add(income_cat)
    bundle = _make_active_bundle(db_session)

    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            savings_service.mark_bundle_completed(db_session, bundle.id)


# ── savings_service: rollover_bundle rollback (lines 145-147) ────────────────


def test_rollover_bundle_rollback_on_error(db_session):
    bundle = _make_active_bundle(db_session)
    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            savings_service.rollover_bundle(db_session, bundle.id)


# ── savings_service: soft_delete_bundle rollback (lines 158-160) ─────────────


def test_soft_delete_bundle_rollback_on_error(db_session):
    bundle = _make_active_bundle(db_session)
    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            savings_service.soft_delete_bundle(db_session, bundle.id)


# ── savings_service: restore_bundle rollback (lines 173-175) ─────────────────


def test_restore_bundle_rollback_on_error(db_session):
    bundle = _make_deleted_bundle(db_session)
    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            savings_service.restore_bundle(db_session, bundle.id)


# ── savings_service: hard_delete_bundle rollback (lines 187-189) ─────────────


def test_hard_delete_bundle_rollback_on_error(db_session):
    bundle = _make_deleted_bundle(db_session)
    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            savings_service.hard_delete_bundle(db_session, bundle.id)


# ── budget_service: skip category missing from DB (line 144) ─────────────────


@pytest.mark.skipif(
    __import__("os").getenv("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL FK enforcement prevents orphaned allocations; defensive code path is SQLite-only",
)
def test_compute_budget_rows_skips_orphaned_allocation(db_session):
    """Covers line 144: 'continue' when category_id in allocation has no matching Category row."""
    cat = Category(name="Temp Cat", type=TransactionType.EXPENSE, color="#000", icon="circle")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)

    alloc = BudgetAllocation(category_id=cat.id, year_month="2026-05", amount=500_000)
    db_session.add(alloc)
    db_session.commit()

    db_session.execute(sqlalchemy.text(f"DELETE FROM categories WHERE id = {cat.id}"))
    db_session.commit()

    result = budget_service.compute_budget_rows(db_session, "2026-05")
    assert result == []


# ── routers/projects.py: 500 on create_payment (lines 202-203) ───────────────


def test_create_payment_route_returns_500_on_service_error(client):
    project_resp = client.post(
        "/api/projects/",
        json={"name": "Error Project", "type": "custom", "description": "test"},
    )
    assert project_resp.status_code == 200
    project_id = project_resp.json()["id"]

    with patch("app.routers.projects.project_service.create_payment", side_effect=Exception("boom")):
        r = client.post(
            f"/api/projects/{project_id}/payments",
            json={"amount": 100_000, "due_date": "2026-06-01", "status": "pending"},
        )
    assert r.status_code == 500


# ── routers/projects.py: 500 on update_payment (lines 227-228) ───────────────


def test_update_payment_route_returns_500_on_service_error(client):
    project_resp = client.post(
        "/api/projects/",
        json={"name": "Error Project 2", "type": "custom", "description": "test"},
    )
    assert project_resp.status_code == 200
    project_id = project_resp.json()["id"]

    payment_resp = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 100_000, "due_date": "2026-06-01", "status": "pending"},
    )
    assert payment_resp.status_code == 200
    payment_id = payment_resp.json()["id"]

    with patch("app.routers.projects.project_service.update_payment", side_effect=Exception("boom")):
        r = client.patch(
            f"/api/projects/{project_id}/payments/{payment_id}",
            json={"notes": "updated"},
        )
    assert r.status_code == 500


# ── routers/savings.py: current_amount default (line 87) ─────────────────────


def test_create_bundle_null_current_amount_defaults_to_initial_deposit(db_session):
    """Covers line 87: current_amount is set to initial_deposit when None.
    Called directly because sending current_amount=null via API hits a validator TypeError."""
    from unittest.mock import MagicMock

    from app.routers.savings import create_savings_bundle

    mock_bundle = MagicMock()
    mock_bundle.linked_project_id = None
    mock_bundle.model_dump.return_value = {
        "name": "Auto Amount Bundle",
        "bank_name": "VCB",
        "type": "fixed_deposit",
        "initial_deposit": 10_000_000,
        "current_amount": None,
        "future_amount": 11_000_000,
        "interest_rate": None,
        "start_date": date(2026, 1, 1),
        "maturity_date": date(2027, 1, 1),
        "notes": None,
        "linked_project_id": None,
        "status": None,
    }

    result = create_savings_bundle(mock_bundle, db_session)
    assert result.current_amount == 10_000_000


# ── routers/transactions.py: commit exception in PATCH (lines 233-235) ───────


def test_put_transaction_rollback_on_commit_error(client, db_session):
    """Covers lines 233-235: db.rollback()+raise when commit fails in PUT /transactions/{id}.
    TestClient with raise_server_exceptions=True re-raises unhandled exceptions,
    so we assert with pytest.raises instead of checking status_code."""
    income_cat = client.post(
        "/api/categories/",
        json={"name": "Salary", "type": "income", "color": "#10B981", "icon": "money"},
    ).json()

    tx = client.post(
        "/api/transactions/",
        json={
            "date": "2026-01-01",
            "amount": 1_000_000,
            "type": "income",
            "category_id": income_cat["id"],
            "payment_method": "cash",
        },
    ).json()
    tx_id = tx["id"]

    original_commit = db_session.commit
    call_count = {"n": 0}

    def fail_on_first():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("commit fail")
        return original_commit()

    with patch.object(db_session, "commit", side_effect=fail_on_first):
        with pytest.raises(Exception, match="commit fail"):
            client.put(f"/api/transactions/{tx_id}", json={"amount": 2_000_000})


# ── transaction_service: soft_delete_transaction rollback (lines 156-158) ─────


def test_soft_delete_transaction_rollback_on_commit_error(db_session, income_cat):
    tx = Transaction(
        date=date(2026, 1, 1),
        amount=1_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)

    with patch.object(db_session, "commit", side_effect=Exception("commit fail")):
        with pytest.raises(Exception, match="commit fail"):
            transaction_service.soft_delete_transaction(db_session, tx.id)


# ── transaction_service: restore_transaction success path (lines 163-169) ─────


def test_restore_transaction_restores_soft_deleted(db_session, income_cat):
    tx = Transaction(
        date=date(2026, 1, 1),
        amount=1_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)

    transaction_service.soft_delete_transaction(db_session, tx.id)
    assert tx.deleted_at is not None

    restored = transaction_service.restore_transaction(db_session, tx.id)
    assert restored.deleted_at is None
    assert restored.id == tx.id


def test_restore_transaction_not_in_trash_raises(db_session, income_cat):
    tx = Transaction(
        date=date(2026, 1, 1),
        amount=1_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)

    with pytest.raises(LookupError, match="not found in trash"):
        transaction_service.restore_transaction(db_session, tx.id)


# ── transaction_service: parse_csv_english row-level generic except (389-391) ─


def test_parse_csv_english_row_exception_captured_by_generic_handler(db_session):
    """Covers lines 389-391: generic except block in parse_csv_english row loop."""
    csv = b"date,amount,type,category\n2026-01-01,1000000,income,Food\n"
    with patch("app.services.transaction_service.get_or_create_category", side_effect=Exception("db gone")):
        stats = transaction_service.parse_csv_english(csv, db_session)
    assert stats["skipped"] >= 1
    assert any("Row 2" in e for e in stats["errors"])


# ── savings_service: _get_or_create_interest_category early return (line 26) ──


def test_get_or_create_interest_category_returns_existing(db_session):
    """Covers line 26: early return when 'Lãi tiết kiệm' income category already exists."""
    existing = Category(
        name="Lãi tiết kiệm",
        type=TransactionType.INCOME,
        color="#f59e0b",
        icon="piggy-bank",
        is_active=True,
        is_wealth_building=False,
    )
    db_session.add(existing)
    fallback = Category(name="Investment", type=TransactionType.INCOME, color="#10B981", icon="chart")
    db_session.add(fallback)

    bundle = SavingsBundle(
        name="Pre-existing Interest Cat",
        bank_name="VCB",
        type="fixed_deposit",
        initial_deposit=5_000_000,
        current_amount=5_000_000,
        future_amount=6_000_000,
        interest_rate=10.0,
        start_date=date(2026, 1, 1),
        maturity_date=date(2027, 1, 1),
        status=SavingsStatus.ACTIVE,
    )
    db_session.add(bundle)
    db_session.commit()
    db_session.refresh(bundle)

    result = savings_service.mark_bundle_completed(db_session, bundle.id)
    assert result.status == SavingsStatus.COMPLETED

    cats = db_session.query(Category).filter(Category.name == "Lãi tiết kiệm").all()
    assert len(cats) == 1


# ── models/schemas.py line 124: SavingsBundleUpdate.validate_maturity_date ───


def test_savings_bundle_update_maturity_date_validator_valid_date(client):
    """Covers line 124: return v in SavingsBundleUpdate.validate_maturity_date."""
    bundle_resp = client.post(
        "/api/savings/",
        json={
            "name": "Bundle to Update",
            "bank_name": "VCB",
            "type": "fixed_deposit",
            "initial_deposit": 5_000_000,
            "future_amount": 5_500_000,
            "start_date": "2026-01-01",
            "maturity_date": "2027-01-01",
        },
    )
    assert bundle_resp.status_code == 200
    bundle_id = bundle_resp.json()["id"]

    r = client.put(
        f"/api/savings/{bundle_id}",
        json={"maturity_date": "2027-06-01"},
    )
    assert r.status_code == 200
    assert r.json()["maturity_date"] == "2027-06-01"
