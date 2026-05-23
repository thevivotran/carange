"""Split savings maturity income into principal return + interest transactions

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-23
"""

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Seed "Lãi tiết kiệm" income category ──────────────────────────────
    existing = conn.execute(
        sa.text("SELECT id FROM categories WHERE name = 'Lãi tiết kiệm' AND type = 'income'")
    ).fetchone()
    if existing:
        interest_cat_id = existing[0]
    else:
        conn.execute(
            sa.text(
                "INSERT INTO categories (name, type, color, icon, is_active, is_wealth_building, created_at)"
                " VALUES ('Lãi tiết kiệm', 'income', '#f59e0b', 'piggy-bank', 1, 0, CURRENT_TIMESTAMP)"
            )
        )
        interest_cat_id = conn.execute(sa.text("SELECT last_insert_rowid()")).scalar()

    # ── 2. Migrate existing maturity income transactions ──────────────────────
    # Find all savings-related income transactions linked to a completed bundle
    rows = conn.execute(
        sa.text(
            "SELECT t.id, t.savings_bundle_id, b.future_amount, b.initial_deposit"
            " FROM transactions t"
            " JOIN savings_bundles b ON b.id = t.savings_bundle_id"
            " WHERE t.type = 'income'"
            "   AND t.is_savings_related = 1"
            "   AND t.savings_bundle_id IS NOT NULL"
            "   AND t.deleted_at IS NULL"
        )
    ).fetchall()

    for tx_id, bundle_id, future_amount, initial_deposit in rows:
        interest = (future_amount or 0) - (initial_deposit or 0)
        if interest < 0:
            interest = 0
        needs_review = 1 if interest <= 0 else 0
        conn.execute(
            sa.text(
                "UPDATE transactions"
                " SET amount = :amount,"
                "     is_savings_related = 0,"
                "     category_id = :cat_id,"
                "     needs_review = :nr"
                " WHERE id = :tx_id"
            ),
            {"amount": interest, "cat_id": interest_cat_id, "nr": needs_review, "tx_id": tx_id},
        )


def downgrade() -> None:
    # Restoring the original full-amount transactions is not safe without
    # knowing per-bundle amounts at the time of migration; downgrade is a no-op.
    pass
