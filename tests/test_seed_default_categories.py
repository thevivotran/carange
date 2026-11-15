"""Tests for first-run seeding in main.seed_default_categories()."""

import pytest
from sqlalchemy.orm import sessionmaker

from app.services.settings_service import get_setting


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
    """Override the global _no_seed: this file calls seed_default_categories()
    directly (against the isolated test engine), so the global no-op patch
    from conftest must not apply here."""
    from app.services import scheduler as _scheduler_mod

    monkeypatch.setattr(_scheduler_mod, "start_scheduler", lambda: None)


def test_fresh_install_seeds_categories_and_simple_dashboard_default(db_session, monkeypatch):
    import main
    from app.models.database import Category

    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

    assert db_session.query(Category).count() == 0

    main.seed_default_categories()

    assert db_session.query(Category).count() > 0
    assert get_setting(db_session, "dashboard_layout", "full") == "simple"


def test_existing_install_keeps_dashboard_default_untouched(db_session, monkeypatch, income_cat):
    import main

    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_session.get_bind()))

    main.seed_default_categories()

    # Categories already existed — seeding (and the new dashboard default) is skipped,
    # so existing self-hosters keep seeing whatever they were used to ("full" fallback).
    assert get_setting(db_session, "dashboard_layout", "full") == "full"


def test_install_with_only_migration_seeded_category_still_seeds_defaults(db_session, monkeypatch):
    """Alembic 0004 inserts "Lãi tiết kiệm" on every fresh database, so a brand-new
    install never has zero categories — it must still get the defaults."""
    import main
    from app.models.database import Category, TransactionType

    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=db_session.get_bind()))
    db_session.add(Category(name="Lãi tiết kiệm", type=TransactionType.INCOME, color="#f59e0b", icon="piggy-bank"))
    db_session.commit()

    main.seed_default_categories()

    assert db_session.query(Category).filter(Category.type == TransactionType.EXPENSE).count() > 0
    assert db_session.query(Category).filter(Category.name == "Lãi tiết kiệm").count() == 1
    assert get_setting(db_session, "dashboard_layout", "full") == "simple"
