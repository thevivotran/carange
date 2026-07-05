"""feat: add income_sources and compensation_events tables

Tracks the quality/trajectory of each household income stream (contract
terms, comp reviews, vesting, replaceability) — episodic state that the
existing recurring cash-flow forecast (transaction_templates) has no room
for. One row per income stream per person, plus a timeline of comp events
against each, mirroring the financial_projects / project_payments shape.

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "income_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("employer", sa.String(200), nullable=True),
        sa.Column("role", sa.String(200), nullable=True),
        sa.Column("income_type", sa.String(), nullable=False),
        sa.Column("contract_type", sa.String(50), nullable=True),
        sa.Column("base_amount_monthly", sa.Numeric(18, 0), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("started_at", sa.Date(), nullable=True),
        sa.Column("ended_at", sa.Date(), nullable=True),
        sa.Column("market_rate_notes", sa.Text(), nullable=True),
        sa.Column("replaceability_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_income_sources_id", "income_sources", ["id"])
    op.create_index("ix_income_sources_user_id", "income_sources", ["user_id"])

    op.create_table(
        "compensation_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("income_source_id", sa.Integer(), sa.ForeignKey("income_sources.id"), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("amount_delta", sa.Numeric(18, 0), nullable=True),
        sa.Column("new_base_amount", sa.Numeric(18, 0), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_compensation_events_id", "compensation_events", ["id"])
    op.create_index("ix_compensation_events_source_date", "compensation_events", ["income_source_id", "event_date"])


def downgrade() -> None:
    op.drop_table("compensation_events")
    op.drop_table("income_sources")
