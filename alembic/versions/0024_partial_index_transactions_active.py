"""perf: make ix_transactions_date_type_savings_category a partial index

Nearly every query against `transactions` filters on `deleted_at IS NULL`
(soft-deleted rows are the rare exception). Recreating the fiscal
aggregation index from migration 0022 as a partial index restricts it to
live rows, making it smaller and more selective for the hot path.

PostgreSQL-only (partial indexes with `postgresql_where` have no SQLite
equivalent honored by Alembic, so this is a no-op on SQLite).

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_index("ix_transactions_date_type_savings_category", table_name="transactions")
    op.create_index(
        "ix_transactions_date_type_savings_category",
        "transactions",
        ["date", "type", "is_savings_related", "category_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_index("ix_transactions_date_type_savings_category", table_name="transactions")
    op.create_index(
        "ix_transactions_date_type_savings_category",
        "transactions",
        ["date", "type", "is_savings_related", "category_id"],
    )
