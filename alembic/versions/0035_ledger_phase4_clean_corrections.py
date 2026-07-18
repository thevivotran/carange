"""fix(ledger): Phase-4 clean corrections — savings/investment roles + orphan payouts

Data-only migration (no schema change). Three "clean" corrections that the
unified ledger (app/services/ledger.py) relies on. All matches are by
name/type (never hard IDs) so this is environment-agnostic and idempotent —
on a fresh DB (e.g. carange_test) every statement updates 0 rows, which is
expected and fine.

1. The INCOME "Tiết kiệm" category has no kpi_role, so non-bundle-linked
   savings returns leaked into EXTERNAL income and inflated net_family_surplus.
   Give it kpi_role='liquid_savings' (the EXPENSE one already got it in 0021).

2. The "Đầu tư" categories (expense + income) had no role and were classified
   only by name-match. Formalize kpi_role='investment' as the primary signal.

3. Two savings payouts ("Carange 10", "Carange 12") were booked as plain
   income, unlinked from their bundle. Link each to its SavingsBundle and flag
   is_savings_related so they classify as LIQUID_SAVINGS (a transfer) instead
   of EXTERNAL income.

NOT touched here (separate reviewed pass): the liquid_cash bundle
reconciliation — Carange 8's missing deposit and the 9 un-migrated early
bundles.

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-18
"""

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. INCOME "Tiết kiệm" → liquid_savings (only if unset; expense one already set in 0021)
    op.execute(
        """
        UPDATE categories
        SET kpi_role = 'liquid_savings'
        WHERE name = 'Tiết kiệm'
          AND type = 'income'
          AND kpi_role IS NULL
        """
    )

    # 2. "Đầu tư" categories (expense + income) → investment (primary role signal)
    op.execute(
        """
        UPDATE categories
        SET kpi_role = 'investment'
        WHERE name = 'Đầu tư'
          AND kpi_role IS NULL
        """
    )

    # 3. Link the two orphan savings payouts to their bundle + flag as savings.
    #    Correlated subqueries match by name/type so this is portable + idempotent;
    #    on a DB without the matching rows it updates nothing.
    for label in ("Carange 10", "Carange 12"):
        op.execute(
            f"""
            UPDATE transactions
            SET savings_bundle_id = (
                    SELECT id FROM savings_bundles
                    WHERE name = '{label}' AND deleted_at IS NULL
                    LIMIT 1
                ),
                is_savings_related = TRUE
            WHERE description = '{label}'
              AND type = 'income'
              AND savings_bundle_id IS NULL
              AND category_id = (
                    SELECT id FROM categories
                    WHERE name = 'Tiết kiệm' AND type = 'income'
                    LIMIT 1
                )
            """
        )


def downgrade() -> None:
    # Reverse #3: unlink the two payouts (best-effort — only the rows we linked).
    for label in ("Carange 10", "Carange 12"):
        op.execute(
            f"""
            UPDATE transactions
            SET savings_bundle_id = NULL,
                is_savings_related = FALSE
            WHERE description = '{label}'
              AND type = 'income'
              AND savings_bundle_id = (
                    SELECT id FROM savings_bundles
                    WHERE name = '{label}' AND deleted_at IS NULL
                    LIMIT 1
                )
            """
        )

    # Reverse #2: "Đầu tư" investment role back to NULL.
    op.execute(
        """
        UPDATE categories
        SET kpi_role = NULL
        WHERE name = 'Đầu tư'
          AND kpi_role = 'investment'
        """
    )

    # Reverse #1: INCOME "Tiết kiệm" liquid_savings role back to NULL.
    op.execute(
        """
        UPDATE categories
        SET kpi_role = NULL
        WHERE name = 'Tiết kiệm'
          AND type = 'income'
          AND kpi_role = 'liquid_savings'
        """
    )
