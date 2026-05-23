"""Normalize type/status enum columns to lowercase to match SQLAlchemy enum values

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-23
"""

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    conn = op.get_bind()

    # Categories: type column
    conn.execute(sa.text("UPDATE categories SET type = LOWER(type) WHERE type != LOWER(type)"))

    # Transactions: type column
    conn.execute(sa.text("UPDATE transactions SET type = LOWER(type) WHERE type != LOWER(type)"))

    # Savings bundles: type and status columns
    conn.execute(sa.text("UPDATE savings_bundles SET type = LOWER(type) WHERE type != LOWER(type)"))
    conn.execute(sa.text("UPDATE savings_bundles SET status = LOWER(status) WHERE status != LOWER(status)"))


def downgrade() -> None:
    pass
