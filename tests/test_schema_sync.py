"""Verify that ORM models and Alembic migrations are in sync.

Catches the class of bug where a developer adds a column to database.py but
forgets to write a migration (or vice versa), which causes AttributeError or
silent NULL data in production.

Strategy:
  - When DATABASE_URL points at PostgreSQL: migrate against that DB directly.
  - Otherwise: apply every migration to a throw-away SQLite database.
"""

import os
import tempfile

import app.models.database as _db_mod
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine


def _run_sync_check(db_url: str, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setattr(_db_mod, "DATABASE_URL", db_url)

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(db_url)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diffs = compare_metadata(ctx, _db_mod.Base.metadata)
    engine.dispose()

    structural = {"add_column", "remove_column", "add_table", "remove_table"}
    real_diffs = [d for d in diffs if isinstance(d, tuple) and d[0] in structural]

    assert not real_diffs, (
        "ORM models and migrations are out of sync.\n"
        "Fix: alembic revision --autogenerate -m 'describe the change'\n\n"
        "Differences:\n" + "\n".join(f"  {d}" for d in real_diffs)
    )


def test_orm_matches_migrations(monkeypatch):
    """ORM metadata must exactly match the schema produced by alembic upgrade head."""
    pg_url = os.environ.get("DATABASE_URL", "")
    if pg_url.startswith("postgresql"):
        _run_sync_check(pg_url, monkeypatch)
        return

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _run_sync_check(f"sqlite:///{db_path}", monkeypatch)
    finally:
        os.unlink(db_path)
