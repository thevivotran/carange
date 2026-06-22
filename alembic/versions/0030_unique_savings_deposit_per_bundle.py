"""unique: prevent duplicate initial-deposit transactions per savings bundle

A real bug surfaced in production (2026-06-22): the same 20M deposit into
Carange 14 was recorded twice because (a) the transaction form and the
savings page could both auto-create a SavingsBundle, and (b) there was no
DB-level guard against a second initial-deposit transaction linking to the
same bundle.

Service-layer dedup was added in transaction_service.create_transaction
and routers/savings.py:create_savings_bundle to find an existing active
bundle by case-insensitive name+bank and link the new transaction to it
instead of creating a duplicate bundle.

This migration is the belt-and-suspenders layer: a partial UNIQUE index
restricted to the initial-deposit description pattern. One bundle can
have at most one live "Initial deposit: ..." transaction, but is free to
accumulate any number of additional "Deposit: ..." top-ups (the
add_bundle_deposit route) and rollover-created "Initial deposit: ..."
transactions on a fresh bundle (new id, so unrelated).

PostgreSQL-only (partial UNIQUE indexes have no SQLite equivalent
honored by Alembic; tests run with a no-op on SQLite).

Revision ID: 0030
Revises: 0029
Create Date: 2026-06-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Clean up any pre-existing duplicate initial-deposit transactions that
    # would otherwise block index creation. Strategy: for each
    # (savings_bundle_id) with more than one active "Initial deposit: ..."
    # transaction, soft-delete all but the most recent one. Matches the
    # production data fix from 2026-06-22 where the orphan was hard-deleted.
    op.execute(
        sa.text(
            """
            UPDATE transactions AS t
            SET deleted_at = NOW()
            WHERE t.deleted_at IS NULL
              AND t.type = 'expense'
              AND t.is_savings_related = true
              AND t.savings_bundle_id IS NOT NULL
              AND t.description LIKE 'Initial deposit: %'
              AND t.id NOT IN (
                SELECT DISTINCT ON (savings_bundle_id) id
                FROM transactions
                WHERE deleted_at IS NULL
                  AND type = 'expense'
                  AND is_savings_related = true
                  AND savings_bundle_id IS NOT NULL
                  AND description LIKE 'Initial deposit: %'
                ORDER BY savings_bundle_id, created_at DESC
              )
            """
        )
    )

    op.create_index(
        "uq_initial_deposit_per_bundle",
        "transactions",
        ["savings_bundle_id"],
        unique=True,
        postgresql_where=sa.text(
            "savings_bundle_id IS NOT NULL "
            "AND type = 'expense' "
            "AND is_savings_related = true "
            "AND deleted_at IS NULL "
            "AND description LIKE 'Initial deposit: %'"
        ),
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_index("uq_initial_deposit_per_bundle", table_name="transactions")
