"""Fix NULL created_at on categories inserted via raw SQL in migration 0004

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-23
"""

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE categories SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))


def downgrade() -> None:
    pass
