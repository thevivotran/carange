"""Baseline schema: capture all ad-hoc column additions from legacy _migrate_db

Revision ID: 0001
Revises:
Create Date: 2026-05-17
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    conn = op.get_bind()

    # ── transactions: ad-hoc columns added over sprints 1-2 ─────────────────
    tx_cols = {r[1] for r in conn.execute(sa.text("PRAGMA table_info(transactions)"))}

    cols_to_add = [
        ("is_advance", "BOOLEAN DEFAULT 0"),
        ("advance_settled", "BOOLEAN DEFAULT 0"),
        ("source", "VARCHAR(30) DEFAULT 'manual'"),
        ("import_job_id", "INTEGER"),
        ("confidence_score", "REAL"),
        ("needs_review", "BOOLEAN NOT NULL DEFAULT 0"),
        ("deleted_at", "DATETIME"),
    ]
    for col_name, col_def in cols_to_add:
        if col_name not in tx_cols:
            conn.execute(sa.text(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_def}"))

    # ── transaction_audit_logs (may not exist on fresh installs) ────────────
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS transaction_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL REFERENCES transactions(id),
            changed_at DATETIME NOT NULL,
            field_name VARCHAR(100) NOT NULL,
            old_value TEXT,
            new_value TEXT
        )
        """)
    )

    # ── indexes ──────────────────────────────────────────────────────────────
    existing_idx = {r[0] for r in conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='index'"))}
    if "ix_transactions_type_date" not in existing_idx:
        conn.execute(sa.text("CREATE INDEX ix_transactions_type_date ON transactions (type, date)"))
    if "ix_transactions_category_id" not in existing_idx:
        conn.execute(sa.text("CREATE INDEX ix_transactions_category_id ON transactions (category_id)"))

    # ── category name normalisation ──────────────────────────────────────────
    conn.execute(sa.text("UPDATE categories SET name = 'Chi phí khác' WHERE name = 'Khác' AND type = 'EXPENSE'"))
    conn.execute(sa.text("UPDATE categories SET name = 'Thu nhập khác' WHERE name = 'Khác' AND type = 'INCOME'"))


def downgrade() -> None:
    pass  # baseline is irreversible
