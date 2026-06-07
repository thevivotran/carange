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
