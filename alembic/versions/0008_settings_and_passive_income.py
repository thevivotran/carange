"""Add settings table and Category.is_passive_income flag

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-23
"""

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op
from datetime import datetime, timezone


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        sa.text("INSERT INTO settings (key, value, updated_at) VALUES ('savings_target_pct', '25', :now)"),
        {"now": now},
    )

    op.add_column("categories", sa.Column("is_passive_income", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_column("categories", "is_passive_income")
