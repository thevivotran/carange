"""perf(fiscal): composite index supporting ORM fallback for non-default pay-cycle days

Adds ix_transactions_date_type_savings_category covering the
(date, type, is_savings_related, category_id) filter+group-by pattern used
by the dashboard/budget live-aggregation fallback when month_start_day != 1
(the calendar-month matview can't be used for custom fiscal windows).

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-11
"""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_transactions_date_type_savings_category",
        "transactions",
        ["date", "type", "is_savings_related", "category_id"],
    )


def downgrade():
    op.drop_index("ix_transactions_date_type_savings_category", table_name="transactions")
