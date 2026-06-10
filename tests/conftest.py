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

from app.models.database import Base, get_db, Category, TransactionType, User
from main import app


def _make_profile_ctx(*, nav_items=None, sections=None):
    """Build a stub ProfileContext (everything visible unless narrowed)."""
    from app.services import dashboard_layout as dl
    from app.services.profiles import ProfileContext

    stub = User(id=1, name="Test", color="#2563EB")
    return ProfileContext(
        user=stub,
        visible_nav_items=nav_items if nav_items is not None else dl.NAV_CORE | frozenset(dl.TOGGLEABLE_NAV_ITEMS),
        visible_sections=sections if sections is not None else frozenset(dl.TOGGLEABLE_SECTIONS),
    )


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
        # Refresh MATVIEW so the next test starts with empty aggregates
        try:
            conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_totals"))
            conn.commit()
        except Exception:
            pass


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
def _bypass_profile(monkeypatch):
    """Run every test as a stub profile with all nav items/sections visible.

    ProfileMiddleware resolves profiles via profiles.resolve_request_context
    (a module-attribute alias), so this single patch keeps all route tests
    working without cookies. visible_sections == PRESETS['full'] preserves
    the pre-profile dashboard assertions. Tests that exercise the real
    cookie flow use the profile_client fixture instead.
    """
    from app.services import profiles as profiles_service

    ctx = _make_profile_ctx()
    monkeypatch.setattr(profiles_service, "resolve_request_context", lambda request: ctx)


@pytest.fixture()
def set_profile_ctx(monkeypatch):
    """Narrow the stub profile's visible nav items/sections for route tests."""

    def _set(*, nav_items=None, sections=None):
        from app.services import profiles as profiles_service

        ctx = _make_profile_ctx(nav_items=nav_items, sections=sections)
        monkeypatch.setattr(profiles_service, "resolve_request_context", lambda request: ctx)

    return _set


@pytest.fixture()
def profile_row(db_session):
    """A real users row matching the stub profile (id=1) — needed by tests
    that write user_settings, so the FK holds on PostgreSQL test runs."""
    user = User(id=1, name="Test", color="#2563EB")
    db_session.add(user)
    db_session.commit()
    return user


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
        # Real PostgreSQL — catches dialect bugs; isolate via TRUNCATE after each test.
        # Attach an after_commit hook that refreshes mv_monthly_totals so dashboard
        # tests see fresh data immediately after inserting transactions.
        from sqlalchemy import event as sa_event

        Session = sessionmaker(autocommit=False, autoflush=False, bind=_pg_engine)
        session = Session()

        @sa_event.listens_for(session, "after_commit")
        def _refresh_mv(s):
            try:
                with _pg_engine.connect() as conn:
                    conn.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_totals"))
                    conn.commit()
            except Exception:
                pass

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


@pytest.fixture()
def profile_client(db_session, monkeypatch):
    """TestClient with REAL profile-cookie resolution backed by the test DB.

    Undoes the autouse _bypass_profile stub and points the resolver's
    SessionLocal at the per-test session (safe: StaticPool in-memory engine).
    """
    from app.services import profiles as profiles_service

    monkeypatch.setattr(profiles_service, "resolve_request_context", profiles_service._real_resolve_request_context)
    monkeypatch.setattr(profiles_service, "SessionLocal", lambda: db_session)
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
        name="Tiết kiệm",
        type=TransactionType.EXPENSE,
        color="#3B82F6",
        icon="piggy-bank",
        is_wealth_building=True,
        kpi_role="liquid_savings",
    )
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def bds_cat(db_session):
    """Bất động sản expense category — used for savings rate and critical check."""
    cat = Category(
        name="Bất động sản",
        type=TransactionType.EXPENSE,
        color="#8B5CF6",
        icon="home",
        is_wealth_building=True,
        kpi_role="real_estate",
    )
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def _refresh_matview_pg(db) -> None:
    """Synchronously refresh mv_monthly_totals so dashboard tests see fresh data.

    Only runs when the test DB is PostgreSQL and the MATVIEW exists.
    """
    if not _is_pg:
        return
    try:
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_totals"))
        db.commit()
    except Exception:
        pass


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
    _refresh_matview_pg(db)
    return t
