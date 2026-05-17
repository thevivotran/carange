"""perf: add P0 indexes for dashboard query performance

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial index: active transactions by date+type — covers all 12-month trend,
    # summary, expense-by-category, and savings queries (skips soft-deleted rows).
    op.create_index(
        "ix_tx_active_date_type",
        "transactions",
        ["date", "type", "is_savings_related"],
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )

    # Unique composite index: fixes the full-scan correlated subquery in
    # compute_budget_rows CTE (120 executions per dashboard render).
    op.create_index(
        "ix_budget_alloc_cat_month",
        "budget_allocations",
        ["category_id", "year_month"],
        unique=True,
    )

    # Covers BDS payment queries: next pending payment, YTD paid/planned,
    # completion date (all filter on project_id + status + due_date).
    op.create_index(
        "ix_project_payments_proj_status_date",
        "project_payments",
        ["project_id", "status", "due_date"],
    )

    # Covers maturity alert and upcoming maturities queries.
    op.create_index(
        "ix_savings_bundles_status_deleted_maturity",
        "savings_bundles",
        ["status", "deleted_at", "maturity_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_savings_bundles_status_deleted_maturity", table_name="savings_bundles")
    op.drop_index("ix_project_payments_proj_status_date", table_name="project_payments")
    op.drop_index("ix_budget_alloc_cat_month", table_name="budget_allocations")
    op.drop_index("ix_tx_active_date_type", table_name="transactions")
