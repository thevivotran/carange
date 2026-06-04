"""Add ai_insights table for cached LLM-generated pulse insights

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-04
"""

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.create_table(
        "ai_insights",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("insight_type", sa.String(50), nullable=False, unique=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger_transaction_id", sa.Integer, sa.ForeignKey("transactions.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("ai_insights")
