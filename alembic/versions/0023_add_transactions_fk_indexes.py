"""perf: indexes on transactions.project_id and transactions.savings_bundle_id

Adds indexes for the FK columns used to filter transactions by linked
financial project or savings bundle (projects/savings fragments).

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-12
"""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_transactions_project_id", "transactions", ["project_id"])
    op.create_index("ix_transactions_savings_bundle_id", "transactions", ["savings_bundle_id"])


def downgrade():
    op.drop_index("ix_transactions_project_id", table_name="transactions")
    op.drop_index("ix_transactions_savings_bundle_id", table_name="transactions")
