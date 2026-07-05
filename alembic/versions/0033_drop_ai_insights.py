"""Drop ai_insights table — Pulse AI insight feature (weekly digest, budget
advisor) removed. Superseded by ad hoc analysis via the carange-readonly MCP
connection; the self-hosted LLM backing it (Qwen3.6 / OpenCode Go) is being
retired.

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-05
"""

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.drop_table("ai_insights")


def downgrade() -> None:
    op.create_table(
        "ai_insights",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("insight_type", sa.String(50), nullable=False, unique=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger_transaction_id", sa.Integer, sa.ForeignKey("transactions.id"), nullable=True),
    )
