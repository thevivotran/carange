"""fix: attach SERIAL sequences to INTEGER primary keys missing a default

When the PostgreSQL DB was bootstrapped from a SQLite migration (tables
already existed when 0001 ran), CREATE TABLE was skipped and the id columns
were left as plain INTEGER NOT NULL without a sequence default.  This causes
NotNullViolation on every INSERT that relies on autoincrement.

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-05
"""

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op

_TABLES_WITH_INT_PK = [
    "categories",
    "import_jobs",
    "transactions",
    "transaction_audit_logs",
    "savings_bundles",
    "financial_projects",
    "project_payments",
    "other_assets",
    "transaction_templates",
    "budget_allocations",
    "transaction_rules",
    "payees",
    "email_ingest_log",
    "notes",
    "ai_insights",
    "period_rollups",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for tbl in _TABLES_WITH_INT_PK:
        if tbl not in existing_tables:
            continue

        # Check whether id already has a DEFAULT (i.e. a sequence is wired up)
        row = bind.execute(
            sa.text(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :tbl AND column_name = 'id'"
            ),
            {"tbl": tbl},
        ).fetchone()

        if row is None or row[0] is not None:
            # Column missing entirely, or already has a default — nothing to do
            continue

        seq = f"{tbl}_id_seq"
        # Create the sequence, seed it from the current max id
        bind.execute(sa.text(f"CREATE SEQUENCE IF NOT EXISTS {seq}"))
        bind.execute(sa.text(f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {tbl}), 0) + 1, false)"))
        bind.execute(sa.text(f"ALTER TABLE {tbl} ALTER COLUMN id SET DEFAULT nextval('{seq}')"))
        bind.execute(sa.text(f"ALTER SEQUENCE {seq} OWNED BY {tbl}.id"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for tbl in _TABLES_WITH_INT_PK:
        if tbl not in existing_tables:
            continue
        bind.execute(sa.text(f"ALTER TABLE {tbl} ALTER COLUMN id DROP DEFAULT"))
        bind.execute(sa.text(f"DROP SEQUENCE IF EXISTS {tbl}_id_seq"))
