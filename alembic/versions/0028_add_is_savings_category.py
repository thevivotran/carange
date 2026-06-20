"""feat: add is_savings_category column to categories

Allows marking a category as savings-related, enabling category-driven
savings detection in later phases. Backfills existing liquid_savings
KPI categories.

Revision ID: 0028
Revises: 0027
Create Date: 2026-06-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("categories") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_savings_category",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )

    op.execute("UPDATE categories SET is_savings_category = 1 WHERE kpi_role = 'liquid_savings'")


def downgrade() -> None:
    with op.batch_alter_table("categories") as batch_op:
        batch_op.drop_column("is_savings_category")
