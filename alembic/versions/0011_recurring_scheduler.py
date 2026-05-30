"""Extend transaction_templates with recurring scheduler fields

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-31
"""

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    with op.batch_alter_table("transaction_templates") as batch_op:
        batch_op.add_column(sa.Column("cadence", sa.String(20), nullable=True))
        batch_op.add_column(sa.Column("next_run_at", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("last_run_at", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("auto_approve", sa.Boolean(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("lead_days", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("transaction_templates") as batch_op:
        batch_op.drop_column("lead_days")
        batch_op.drop_column("auto_approve")
        batch_op.drop_column("last_run_at")
        batch_op.drop_column("next_run_at")
        batch_op.drop_column("cadence")
