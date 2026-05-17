"""Add is_wealth_building to categories, deleted_at to projects and savings bundles

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-17
"""

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    conn = op.get_bind()

    cat_cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(categories)"))}
    if "is_wealth_building" not in cat_cols:
        with op.batch_alter_table("categories") as batch_op:
            batch_op.add_column(sa.Column("is_wealth_building", sa.Boolean(), server_default="0", nullable=False))
        conn.execute(
            sa.text(
                "UPDATE categories SET is_wealth_building = 1"
                " WHERE name IN ('Tiết kiệm', 'Bất động sản') AND type = 'expense'"
            )
        )

    proj_cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(financial_projects)"))}
    if "deleted_at" not in proj_cols:
        with op.batch_alter_table("financial_projects") as batch_op:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))

    sav_cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(savings_bundles)"))}
    if "deleted_at" not in sav_cols:
        with op.batch_alter_table("savings_bundles") as batch_op:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("categories") as batch_op:
        batch_op.drop_column("is_wealth_building")
    with op.batch_alter_table("financial_projects") as batch_op:
        batch_op.drop_column("deleted_at")
    with op.batch_alter_table("savings_bundles") as batch_op:
        batch_op.drop_column("deleted_at")
