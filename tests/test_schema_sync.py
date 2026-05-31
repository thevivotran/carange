"""Verify that ORM models and Alembic migrations are in sync.

Catches the class of bug where a developer adds a column to database.py but
forgets to write a migration (or vice versa), which causes AttributeError or
silent NULL data in production.

Strategy:
  1. Apply every migration to a throw-away SQLite database.
  2. Compare the resulting schema against SQLAlchemy's ORM metadata.
  3. Fail if any column/table additions or removals are detected.
"""

import os
import tempfile

import app.models.database as _db_mod
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine


def test_orm_matches_migrations(monkeypatch):
    """ORM metadata must exactly match the schema produced by alembic upgrade head.

    If this test fails: run 'alembic revision --autogenerate -m "describe change"'
    to generate the missing migration, then commit it alongside the model change.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db_url = f"sqlite:///{db_path}"

        # Patch DATABASE_URL so alembic/env.py picks up the temp DB when it
        # re-executes on command.upgrade (env.py does a fresh import each call)
        monkeypatch.setattr(_db_mod, "DATABASE_URL", db_url)

        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")

        engine = create_engine(db_url)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diffs = compare_metadata(ctx, _db_mod.Base.metadata)
        engine.dispose()

        # Only flag structural changes that cause silent data loss in production.
        # SQLite's reflection produces false positives for nullable mismatches, type
        # aliases (REAL vs Float), and constraints. Modify-* diffs come as lists;
        # structural diffs (add/remove column/table) come as plain tuples.
        structural = {"add_column", "remove_column", "add_table", "remove_table"}
        real_diffs = [d for d in diffs if isinstance(d, tuple) and d[0] in structural]

        assert not real_diffs, (
            "ORM models and migrations are out of sync.\n"
            "Fix: alembic revision --autogenerate -m 'describe the change'\n\n"
            "Differences:\n" + "\n".join(f"  {d}" for d in real_diffs)
        )
    finally:
        os.unlink(db_path)
