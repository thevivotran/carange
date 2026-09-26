"""feat: add spender_user_id to transactions

Lets a transaction be attributed to a household profile (users.id) for
per-person spend analysis. Nullable and unset by default — existing and
newly-imported transactions stay unassigned until a user tags them; NULL is
treated as its own "Unassigned" bucket in reports, never defaulted to a
specific person.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("spender_user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_transactions_spender_user_id", "users", ["spender_user_id"], ["id"])
        batch_op.create_index("ix_transactions_spender_user_id", ["spender_user_id"])


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_index("ix_transactions_spender_user_id")
        batch_op.drop_constraint("fk_transactions_spender_user_id", type_="foreignkey")
        batch_op.drop_column("spender_user_id")
