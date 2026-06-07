"""allow 'sample' as a transaction source

The new opt-in Settings → Sample Data feature tags every synthetic record it
creates with source='sample' so it can be identified and removed cleanly.
PostgreSQL's chk_tx_source CHECK constraint must allow that value (SQLite has
no such constraint, so this is a no-op there).

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-07
"""

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text("""
        ALTER TABLE transactions
        DROP CONSTRAINT IF EXISTS chk_tx_source,
        ADD CONSTRAINT chk_tx_source
        CHECK (source IN (
            'manual','csv','ocr','email','template','sample',
            'timo','shopee','grab','uob','liobank',
            'savings_maturity','project_payment'
        ) OR source IS NULL)
    """)
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bind.execute(
        sa.text("""
        ALTER TABLE transactions
        DROP CONSTRAINT IF EXISTS chk_tx_source,
        ADD CONSTRAINT chk_tx_source
        CHECK (source IN (
            'manual','csv','ocr','email','template',
            'timo','shopee','grab','uob','liobank',
            'savings_maturity','project_payment'
        ) OR source IS NULL)
    """)
    )
