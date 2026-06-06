"""feat(resilience): retry counters, started_at, mv_monthly_totals materialized view

Phase 1 — database-as-queue hardening:
  • import_jobs: add started_at (accurate stuck-job reclaim), retry_count, retry_after
  • email_ingest_log: add retry_count, retry_after

Phase 2 — PostgreSQL MATVIEW for fast dashboard aggregation:
  • mv_monthly_totals: pre-aggregated monthly totals per (month, type, savings, category)
  • Unique index enables REFRESH CONCURRENTLY (readers never block during refresh)

All column additions are dialect-agnostic; MATVIEW is PostgreSQL-only.

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-06
"""

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ── import_jobs additions ─────────────────────────────────────────────────
    op.add_column("import_jobs", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("import_jobs", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("import_jobs", sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True))

    # ── email_ingest_log additions ────────────────────────────────────────────
    op.add_column("email_ingest_log", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("email_ingest_log", sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True))

    if not is_pg:
        return

    # Convert new columns to TIMESTAMPTZ
    for tbl, col in [
        ("import_jobs", "started_at"),
        ("import_jobs", "retry_after"),
        ("email_ingest_log", "retry_after"),
    ]:
        op.execute(sa.text(f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE TIMESTAMPTZ"))

    # ── Materialized view for dashboard aggregations ──────────────────────────
    # COALESCE(is_savings_related, FALSE) and COALESCE(category_id, 0) keep the
    # group keys NULL-free so REFRESH CONCURRENTLY can use the unique index cleanly.
    op.execute(
        sa.text("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_monthly_totals AS
        SELECT
            date_trunc('month', date)::date         AS month,
            type,
            COALESCE(is_savings_related, FALSE)     AS is_savings_related,
            COALESCE(category_id, 0)                AS category_id,
            SUM(amount)::NUMERIC(18,0)              AS total,
            COUNT(*)                                AS tx_count
        FROM transactions
        WHERE deleted_at IS NULL
        GROUP BY 1, 2, 3, 4
        WITH DATA
    """)
    )

    op.execute(
        sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_monthly_totals
        ON mv_monthly_totals(month, type, is_savings_related, category_id)
    """)
    )


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP MATERIALIZED VIEW IF EXISTS mv_monthly_totals"))

    op.drop_column("email_ingest_log", "retry_after")
    op.drop_column("email_ingest_log", "retry_count")
    op.drop_column("import_jobs", "retry_after")
    op.drop_column("import_jobs", "retry_count")
    op.drop_column("import_jobs", "started_at")
