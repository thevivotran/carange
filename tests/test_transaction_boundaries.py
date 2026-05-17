"""Tests for DB transaction boundary safety in multi-step mutations."""


from app.models.database import SavingsBundle, SavingsStatus, ProjectPayment, PaymentStatus


def _bundle_payload(name="Boundary Bundle"):
    return {
        "name": name,
        "bank_name": "VCB",
        "type": "fixed_deposit",
        "initial_deposit": 10_000_000,
        "current_amount": 10_000_000,
        "future_amount": 10_500_000,
        "interest_rate": 5.0,
        "start_date": "2026-01-01",
        "maturity_date": "2026-07-01",
    }


def _project_payload(name="Boundary Project"):
    return {"name": name, "type": "custom", "priority": "low"}


def test_mark_bundle_completed_sets_status(client, db_session):
    """mark-completed must persist COMPLETED status and completed_at."""
    bundle_id = client.post("/api/savings/", json=_bundle_payload()).json()["id"]
    r = client.post(f"/api/savings/{bundle_id}/mark-completed")
    assert r.status_code == 200

    bundle = db_session.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first()
    db_session.refresh(bundle)
    assert bundle.status == SavingsStatus.COMPLETED
    assert bundle.completed_at is not None


def test_bulk_payments_creates_all_atomically(client, db_session):
    """bulk create should produce exactly occurrences payments in one shot."""
    project_id = client.post("/api/projects/", json=_project_payload()).json()["id"]

    r = client.post(
        f"/api/projects/{project_id}/payments/bulk",
        json={
            "amount": 1_000_000,
            "start_date": "2026-06-01",
            "interval": "monthly",
            "occurrences": 3,
        },
    )
    assert r.status_code == 200
    assert len(r.json()) == 3

    count = (
        db_session.query(ProjectPayment).filter(ProjectPayment.project_id == project_id).count()
    )
    assert count == 3


def test_soft_delete_transaction_reverts_payment(client, db_session, expense_cat):
    """Soft-deleting a transaction that funds a ProjectPayment reverts the payment to PENDING."""
    project_id = client.post("/api/projects/", json=_project_payload()).json()["id"]

    payment_id = client.post(
        f"/api/projects/{project_id}/payments",
        json={"amount": 500_000, "status": "pending", "notes": "test"},
    ).json()["id"]

    # Mark paid — this creates a linked transaction
    client.patch(
        f"/api/projects/{project_id}/payments/{payment_id}",
        json={"status": "paid", "category_id": expense_cat.id},
    )

    payment = db_session.query(ProjectPayment).filter(ProjectPayment.id == payment_id).first()
    db_session.refresh(payment)
    assert payment.status == PaymentStatus.PAID
    tx_id = payment.transaction_id
    assert tx_id is not None

    # Soft-delete the transaction — must cascade: payment reverts to PENDING
    r = client.delete(f"/api/transactions/{tx_id}")
    assert r.status_code == 200

    db_session.expire(payment)
    db_session.refresh(payment)
    assert payment.status == PaymentStatus.PENDING
    assert payment.transaction_id is None


def test_rollover_creates_new_bundle_and_completes_old(client, db_session):
    """rollover must complete the old bundle and create a new active one atomically."""
    bundle_id = client.post("/api/savings/", json=_bundle_payload(name="Old Bundle")).json()["id"]

    r = client.post(f"/api/savings/{bundle_id}/rollover")
    assert r.status_code == 200
    new_id = r.json()["id"]
    assert new_id != bundle_id

    old = db_session.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first()
    db_session.refresh(old)
    assert old.status == SavingsStatus.COMPLETED

    new = db_session.query(SavingsBundle).filter(SavingsBundle.id == new_id).first()
    assert new.status == SavingsStatus.ACTIVE
