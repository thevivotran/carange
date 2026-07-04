"""remove: drop the payees table and transactions.payee_id

The payee feature (merchant-alias normalization) never got adopted — only
1 payee row and 1 tagged transaction existed in production. Removed along
with its integration points in the rules engine and ingest pipeline.

Dropping the payee_id column implicitly drops any FK constraint that
depends on it, on both SQLite (batch table recreation) and PostgreSQL, so
no explicit constraint-name drop is needed.

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-04
"""

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_column("payee_id")

    op.drop_table("payees")


def downgrade() -> None:
    op.create_table(
        "payees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_name", sa.String(200), nullable=False, unique=True),
        sa.Column("default_category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("alias_patterns", sa.JSON(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("payee_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_transactions_payee_id", "payees", ["payee_id"], ["id"])
