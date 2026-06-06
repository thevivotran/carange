"""Shared pytest fixtures for Carange tests.

Each test function gets a fresh isolated database:
- SQLite in-memory (default, fast) when TEST_DATABASE_URL is unset
- Real PostgreSQL (catches dialect bugs) when TEST_DATABASE_URL=postgresql://...
"""

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base, get_db, Category, TransactionType
from main import app


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "sqlite:///:memory:")
_is_pg = TEST_DATABASE_URL.startswith("postgresql")

# For PostgreSQL: one engine for the whole session; tables created once.
# For SQLite: engine is created per-test (StaticPool gives a fresh in-memory DB).
_pg_engine = None
if _is_pg:
    _pg_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(_pg_engine)


def _truncate_all_tables() -> None:
    """Wipe all rows after each PG test, preserving schema."""
    table_names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with _pg_engine.connect() as conn:
        conn.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
        conn.commit()


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
    """Suppress lifespan side-effects that bypass the test get_db override.

    seed_default_categories() opens its own SessionLocal() and writes directly
    to the test DB, causing duplicate-name 400s for API-level category tests.
    start_scheduler() opens background DB connections that deadlock TRUNCATE.
    """
    import main
    from app.services import scheduler as _scheduler_mod

    monkeypatch.setattr(main, "seed_default_categories", lambda: None)
    monkeypatch.setattr(_scheduler_mod, "start_scheduler", lambda: None)


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """Reset in-process caches before each test to prevent cross-test contamination."""
    from app.services import dashboard_service, rules_service

    dashboard_service.invalidate_dashboard_cache()
    rules_service.invalidate_payee_cache()
    yield
    dashboard_service.invalidate_dashboard_cache()
    rules_service.invalidate_payee_cache()


@pytest.fixture()
def db_session():
    if not _is_pg:
        # Fresh in-memory SQLite per test — fast, fully isolated
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
    else:
        # Real PostgreSQL — catches dialect bugs; isolate via TRUNCATE after each test
        Session = sessionmaker(autocommit=False, autoflush=False, bind=_pg_engine)
        session = Session()
        try:
            yield session
        finally:
            session.close()
            _truncate_all_tables()


@pytest.fixture()
def client(db_session):
    """TestClient whose every request uses the isolated DB session."""
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
    cat = Category(
        name="Tiết kiệm", type=TransactionType.EXPENSE, color="#3B82F6", icon="piggy-bank", is_wealth_building=True
    )
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def bds_cat(db_session):
    """Bất động sản expense category — used for savings rate and critical check."""
    cat = Category(
        name="Bất động sản", type=TransactionType.EXPENSE, color="#8B5CF6", icon="home", is_wealth_building=True
    )
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
