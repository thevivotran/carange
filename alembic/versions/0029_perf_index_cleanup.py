"""perf: index cleanup — drop low-cardinality bool indexes, add composite indexes

Drop wasteful low-cardinality boolean indexes on transactions
(ix_transactions_needs_review, ix_transactions_is_savings_related) and
replace the full ix_transactions_deleted_at with a partial index that
only covers the rare soft-deleted rows.

Also drop ix_transaction_audit_logs_transaction_id from migration 0017
(single-column FK index) and replace it with a composite index on
(transaction_id, changed_at) that covers audit log lookups sorted by time.

Add a composite index on project_payments(project_id, status) for
dashboard filters that query by project + payment status.

All operations are PostgreSQL-only; partial indexes with postgresql_where
have no SQLite equivalent honored by Alembic.

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _drop_index_safe(conn, base_name: str, table_name: str):
    """Drop an index by base name, also matching pgloader's OID-prefixed names.

    pgloader renames indexes to idx_<oid>_<base_name> during SQLite→PG migration.
    This helper tries both the expected name (fresh PG) and any prefixed variants.
    """
    conn.execute(sa.text(f'DROP INDEX IF EXISTS "{base_name}"'))
    # Also handle pgloader-prefixed names (idx_<oid>_<base_name>)
    rows = conn.execute(
        sa.text(
            "SELECT indexname FROM pg_indexes WHERE tablename = :table AND indexname LIKE :pat AND indexname != :base"
        ),
        {"table": table_name, "pat": f"%\\_{base_name}", "base": base_name},
    ).fetchall()
    for (idx_name,) in rows:
        conn.execute(sa.text(f'DROP INDEX IF EXISTS "{idx_name}"'))


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Drop wasteful low-cardinality boolean indexes (handle pgloader prefix)
    _drop_index_safe(bind, "ix_transactions_needs_review", "transactions")
    _drop_index_safe(bind, "ix_transactions_is_savings_related", "transactions")

    # Drop the full deleted_at index, replace with partial index
    _drop_index_safe(bind, "ix_transactions_deleted_at", "transactions")
    op.create_index(
        "ix_transactions_trash",
        "transactions",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )

    # Drop single-column FK index on audit logs, replace with composite
    op.drop_index("ix_transaction_audit_logs_transaction_id", table_name="transaction_audit_logs")
    op.create_index(
        "ix_audit_logs_txn_changed",
        "transaction_audit_logs",
        ["transaction_id", "changed_at"],
    )

    # Add composite index for project payment dashboard filters
    op.create_index(
        "ix_project_payments_project_status",
        "project_payments",
        ["project_id", "status"],
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Reverse new composite indexes
    op.drop_index("ix_project_payments_project_status", table_name="project_payments")
    op.drop_index("ix_audit_logs_txn_changed", table_name="transaction_audit_logs")

    # Restore the single-column FK index on audit logs
    op.create_index(
        "ix_transaction_audit_logs_transaction_id",
        "transaction_audit_logs",
        ["transaction_id"],
    )

    # Drop partial trash index, restore full deleted_at index
    op.drop_index("ix_transactions_trash", table_name="transactions")
    op.create_index("ix_transactions_deleted_at", "transactions", ["deleted_at"])

    # Restore the dropped boolean indexes
    op.create_index(
        "ix_transactions_is_savings_related",
        "transactions",
        ["is_savings_related"],
    )
    op.create_index(
        "ix_transactions_needs_review",
        "transactions",
        ["needs_review"],
    )
