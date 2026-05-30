"""Add transaction_rules and payees tables; add payee_id FK to transactions

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-31
"""

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.create_table(
        "payees",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("canonical_name", sa.String(200), nullable=False, unique=True),
        sa.Column("default_category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("alias_patterns", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "transaction_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("match_field", sa.String(50), nullable=False),
        sa.Column("match_op", sa.String(20), nullable=False),
        sa.Column("match_value", sa.Text(), nullable=False),
        sa.Column("action_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_matched_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("payee_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_transactions_payee_id", "payees", ["payee_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_constraint("fk_transactions_payee_id", type_="foreignkey")
        batch_op.drop_column("payee_id")

    op.drop_table("transaction_rules")
    op.drop_table("payees")
