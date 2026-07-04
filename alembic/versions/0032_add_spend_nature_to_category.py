"""feat: add spend_nature to categories

Adds a category-level spend_nature classification (recurring/discretionary/
mixed) so reports can distinguish routine spend from lumpy one-offs without
per-transaction tagging (the payee_id precedent showed manual per-transaction
tags don't get adopted — see 0031_drop_payees.py).

Defaults to 'mixed' for all existing rows so nothing errors on migration.

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("spend_nature", sa.String(), nullable=False, server_default="mixed"),
    )


def downgrade() -> None:
    op.drop_column("categories", "spend_nature")
