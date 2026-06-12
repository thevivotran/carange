from datetime import date, timedelta

from app.models.database import (
    FinancialProject,
    PaymentStatus,
    ProjectPayment,
    ProjectStatus,
    ProjectType,
    Transaction,
    TransactionType,
)
from app.services import project_service
from app.services.forecast_service import build_forecast


def _make_project_and_payment(db_session, amount=50_000_000, days_ahead=10):
    project = FinancialProject(
        name="Apartment",
        type=ProjectType.REAL_ESTATE,
        status=ProjectStatus.IN_PROGRESS,
        target_amount=1_000_000_000,
    )
    db_session.add(project)
    db_session.commit()

    payment = ProjectPayment(
        project_id=project.id,
        due_date=date.today() + timedelta(days=days_ahead),
        amount=amount,
        status=PaymentStatus.PENDING,
    )
    db_session.add(payment)
    db_session.commit()
    return project, payment


def test_settle_payment_from_transaction_service(db_session, expense_cat):
    project, payment = _make_project_and_payment(db_session)

    tx = Transaction(
        date=date.today(),
        amount=50_000_000,
        type=TransactionType.EXPENSE,
        category_id=expense_cat.id,
        description="Apartment payment",
    )
    db_session.add(tx)
    db_session.commit()

    updated = project_service.settle_payment_from_transaction(db_session, project, payment, tx)

    assert updated.status == PaymentStatus.PAID
    assert updated.transaction_id == tx.id

    db_session.refresh(project)
    assert project.current_amount == 50_000_000

    db_session.refresh(tx)
    assert tx.project_id == project.id

    result = build_forecast(db_session, horizon_days=90)
    pay_events = [e for e in result["events"] if e["source"] == "project_payment"]
    assert pay_events == []


def test_settle_payment_endpoint(client, db_session, expense_cat):
    project, payment = _make_project_and_payment(db_session)

    tx = Transaction(
        date=date.today(),
        amount=50_000_000,
        type=TransactionType.EXPENSE,
        category_id=expense_cat.id,
        description="Apartment payment",
    )
    db_session.add(tx)
    db_session.commit()

    res = client.post(f"/api/transactions/{tx.id}/settle-payment/{payment.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "paid"
    assert body["transaction_id"] == tx.id


def test_settle_already_paid_payment_returns_400(client, db_session, expense_cat):
    project, payment = _make_project_and_payment(db_session)
    payment.status = PaymentStatus.PAID
    db_session.commit()

    tx = Transaction(
        date=date.today(),
        amount=50_000_000,
        type=TransactionType.EXPENSE,
        category_id=expense_cat.id,
        description="Apartment payment",
    )
    db_session.add(tx)
    db_session.commit()

    res = client.post(f"/api/transactions/{tx.id}/settle-payment/{payment.id}")
    assert res.status_code == 400


def test_match_endpoint_finds_pending_payment(client, db_session):
    project, payment = _make_project_and_payment(db_session, amount=50_000_000, days_ahead=10)

    res = client.get(
        f"/api/projects/{project.id}/payments/match",
        params={"amount": 50_000_000, "date": str(date.today() + timedelta(days=9))},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == payment.id


def test_match_endpoint_no_match_returns_none(client, db_session):
    project, _payment = _make_project_and_payment(db_session, amount=50_000_000, days_ahead=10)

    res = client.get(
        f"/api/projects/{project.id}/payments/match",
        params={"amount": 1_000, "date": str(date.today())},
    )
    assert res.status_code == 200
    assert res.json() is None
