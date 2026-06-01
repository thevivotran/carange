"""perf: add hot-path filter indexes (A1 from codebase review)

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-01
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # transactions — columns filtered on nearly every query
    op.create_index("ix_transactions_deleted_at", "transactions", ["deleted_at"])
    op.create_index("ix_transactions_import_job_id", "transactions", ["import_job_id"])
    op.create_index("ix_transactions_needs_review", "transactions", ["needs_review"])
    op.create_index("ix_transactions_is_savings_related", "transactions", ["is_savings_related"])

    # savings_bundles and financial_projects — dashboard/list status filters
    op.create_index("ix_savings_bundles_status", "savings_bundles", ["status"])
    op.create_index("ix_financial_projects_status", "financial_projects", ["status"])


def downgrade() -> None:
    op.drop_index("ix_financial_projects_status", table_name="financial_projects")
    op.drop_index("ix_savings_bundles_status", table_name="savings_bundles")
    op.drop_index("ix_transactions_is_savings_related", table_name="transactions")
    op.drop_index("ix_transactions_needs_review", table_name="transactions")
    op.drop_index("ix_transactions_import_job_id", table_name="transactions")
    op.drop_index("ix_transactions_deleted_at", table_name="transactions")
