"""feat(kpi-terms): add kpi_role to categories with backfill from known names

Adds kpi_role column to categories table so KPI buckets (liquid_savings,
real_estate) are defined by explicit role assignment rather than hardcoded
category names.

Backfill (PostgreSQL & SQLite):
  • kpi_role='liquid_savings' where name='Tiết kiệm' AND type='expense'
  • kpi_role='real_estate'  where name='Bất động sản' AND type='expense'

Revision ID: 0021
Revises: 0020
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add the column (nullable, no server_default — safe for both SQLite & PG)
    op.add_column("categories", sa.Column("kpi_role", sa.String(20), nullable=True))

    # 2. Backfill from the hardcoded names that the app currently uses
    #    Type column values are lowercase strings ('expense' / 'income').
    op.execute(
        """
        UPDATE categories
        SET kpi_role = 'liquid_savings'
        WHERE name = 'Tiết kiệm'
          AND type = 'expense'
          AND kpi_role IS NULL
        """
    )
    op.execute(
        """
        UPDATE categories
        SET kpi_role = 'real_estate'
        WHERE name = 'Bất động sản'
          AND type = 'expense'
          AND kpi_role IS NULL
        """
    )


def downgrade():
    op.drop_column("categories", "kpi_role")
