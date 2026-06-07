"""Tests for the opt-in sample/demo data feature (Settings → Sample Data)."""

from app.models.database import Category, SavingsBundle, Transaction, TransactionType
from app.services.sample_data_service import (
    SAMPLE_SOURCE,
    has_sample_data,
    load_sample_data,
    remove_sample_data,
)


def _seed_categories(db_session):
    db_session.add_all(
        [
            Category(name="Salary", type=TransactionType.INCOME, color="#10B981", icon="money"),
            Category(name="Food & Dining", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils"),
            Category(name="Transportation", type=TransactionType.EXPENSE, color="#F59E0B", icon="car"),
        ]
    )
    db_session.commit()


def test_load_sample_data_creates_records(db_session):
    _seed_categories(db_session)

    assert has_sample_data(db_session) is False
    created = load_sample_data(db_session)

    assert created > 0
    assert has_sample_data(db_session) is True
    assert db_session.query(Transaction).filter(Transaction.source == SAMPLE_SOURCE).count() > 0
    assert db_session.query(SavingsBundle).filter(SavingsBundle.name.like("%Sample%")).count() == 1


def test_load_sample_data_is_idempotent(db_session):
    _seed_categories(db_session)

    first = load_sample_data(db_session)
    second = load_sample_data(db_session)

    assert first > 0
    assert second == 0
    # No duplicate records were created on the second call
    assert db_session.query(Transaction).filter(Transaction.source == SAMPLE_SOURCE).count() == first - 1


def test_load_sample_data_noop_without_categories(db_session):
    assert load_sample_data(db_session) == 0
    assert has_sample_data(db_session) is False


def test_remove_sample_data_deletes_only_tagged_records(db_session):
    _seed_categories(db_session)
    income_cat = db_session.query(Category).filter(Category.name == "Salary").first()

    real_txn = Transaction(
        date=__import__("datetime").date.today(),
        amount=1_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Real paycheck",
        source="manual",
    )
    db_session.add(real_txn)
    db_session.commit()

    load_sample_data(db_session)
    removed = remove_sample_data(db_session)

    assert removed > 0
    assert has_sample_data(db_session) is False
    assert db_session.query(Transaction).filter(Transaction.source == SAMPLE_SOURCE).count() == 0
    assert db_session.query(SavingsBundle).filter(SavingsBundle.name.like("%Sample%")).count() == 0
    # The user's real transaction survives untouched
    assert db_session.query(Transaction).filter(Transaction.id == real_txn.id).count() == 1


def test_remove_sample_data_noop_when_nothing_loaded(db_session):
    assert remove_sample_data(db_session) == 0


def test_settings_load_and_remove_sample_data_routes(client, db_session):
    _seed_categories(db_session)

    load_resp = client.post("/settings/sample-data/load")
    assert load_resp.status_code == 200
    assert "Sample data is loaded" in load_resp.text
    assert has_sample_data(db_session) is True

    remove_resp = client.post("/settings/sample-data/remove")
    assert remove_resp.status_code == 200
    assert "Load sample data" in remove_resp.text
    assert has_sample_data(db_session) is False
