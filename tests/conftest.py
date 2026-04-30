"""Shared pytest fixtures for Carange tests.

Each test function gets a fresh in-memory SQLite database so tests are
fully isolated from the production carange.db and from each other.
"""
import pytest
from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base, get_db, Category, TransactionType
from main import app

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture()
def db_session():
    # StaticPool keeps a single shared connection so all code sees the same
    # in-memory database (default pool opens a new DB per connection).
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """TestClient whose every request uses the isolated in-memory DB."""
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ── Category fixtures ─────────────────────────────────────────────────────────

@pytest.fixture()
def income_cat(db_session):
    cat = Category(name="Salary", type=TransactionType.INCOME, color="#10B981", icon="money")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def expense_cat(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def tiet_kiem_cat(db_session):
    """Tiết kiệm expense category — used for savings rate calculation."""
    cat = Category(name="Tiết kiệm", type=TransactionType.EXPENSE, color="#3B82F6", icon="piggy-bank")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def bds_cat(db_session):
    """Bất động sản expense category — used for savings rate and critical check."""
    cat = Category(name="Bất động sản", type=TransactionType.EXPENSE, color="#8B5CF6", icon="home")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def make_transaction(db, *, date_val, amount, type_, category_id, is_savings_related=False):
    """Helper: insert a Transaction directly and return it."""
    from app.models.database import Transaction
    t = Transaction(
        date=date_val,
        amount=amount,
        type=type_,
        category_id=category_id,
        is_savings_related=is_savings_related,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t
