"""perf: add missing FK and queue-poll indexes

Four indexes that were absent:
  • email_ingest_log(status, retry_after) — queue poll WHERE status='pending' AND retry_after IS NULL
  • import_jobs(status, retry_after)      — same pattern in OCR worker
  • transactions(email_ingest_log_id)     — FK lookup, previously unindexed
  • transaction_audit_logs(transaction_id) — FK lookup for audit cascade, previously unindexed

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-06
"""

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    op.create_index(
        "ix_email_ingest_log_status_retry",
        "email_ingest_log",
        ["status", "retry_after"],
    )
    op.create_index(
        "ix_import_jobs_status_retry",
        "import_jobs",
        ["status", "retry_after"],
    )
    op.create_index(
        "ix_transactions_email_ingest_log_id",
        "transactions",
        ["email_ingest_log_id"],
    )
    op.create_index(
        "ix_transaction_audit_logs_transaction_id",
        "transaction_audit_logs",
        ["transaction_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_transaction_audit_logs_transaction_id", table_name="transaction_audit_logs")
    op.drop_index("ix_transactions_email_ingest_log_id", table_name="transactions")
    op.drop_index("ix_import_jobs_status_retry", table_name="import_jobs")
    op.drop_index("ix_email_ingest_log_status_retry", table_name="email_ingest_log")
