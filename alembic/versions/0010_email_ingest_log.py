"""Add email_ingest_log table and email_ingest_log_id FK on transactions

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-31
"""

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.create_table(
        "email_ingest_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.String(500), nullable=False, unique=True),
        sa.Column("sender", sa.String(200), nullable=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("transaction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )

    with op.batch_alter_table("transactions") as batch_op:
        batch_op.add_column(sa.Column("email_ingest_log_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_transactions_email_ingest_log_id", "email_ingest_log", ["email_ingest_log_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("transactions") as batch_op:
        batch_op.drop_constraint("fk_transactions_email_ingest_log_id", type_="foreignkey")
        batch_op.drop_column("email_ingest_log_id")

    op.drop_table("email_ingest_log")
