"""feat: structured IOU fields on notes

"Money owed" notes were free text, so the app could not total who owes what or turn
a settled IOU into a transaction. Adds counterparty, amount, direction, due date and
settled_at. All nullable — existing notes are untouched and general notes never use
them.

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notes") as batch_op:
        batch_op.add_column(sa.Column("counterparty", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("amount", sa.Numeric(18, 0), nullable=True))
        batch_op.add_column(sa.Column("direction", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notes") as batch_op:
        batch_op.drop_column("settled_at")
        batch_op.drop_column("due_date")
        batch_op.drop_column("direction")
        batch_op.drop_column("amount")
        batch_op.drop_column("counterparty")
