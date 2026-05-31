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

    # ── Fresh-install bootstrap ───────────────────────────────────────────────
    # These tables existed before Alembic was introduced. On a production DB they
    # already exist; on a fresh CI/test DB they must be created first so that
    # subsequent ALTER TABLE statements in this and later migrations can run.
    # Column sets are intentionally minimal — later migrations add the rest.
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS categories (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            name    VARCHAR(100) NOT NULL,
            type    VARCHAR      NOT NULL,
            color   VARCHAR(7)   DEFAULT '#3B82F6',
            icon    VARCHAR(50)  DEFAULT 'circle',
            is_active BOOLEAN    NOT NULL DEFAULT 1,
            created_at DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS financial_projects (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             VARCHAR(200) NOT NULL,
            type             VARCHAR      NOT NULL,
            description      TEXT,
            target_amount    REAL         NOT NULL DEFAULT 0,
            current_amount   REAL         NOT NULL DEFAULT 0,
            priority         VARCHAR      NOT NULL DEFAULT 'medium',
            status           VARCHAR      NOT NULL DEFAULT 'planning',
            deadline         DATE,
            default_category_id INTEGER   REFERENCES categories(id),
            created_at       DATETIME,
            completed_at     DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS savings_bundles (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             VARCHAR(200) NOT NULL,
            bank_name        VARCHAR(100) NOT NULL,
            type             VARCHAR      NOT NULL,
            initial_deposit  REAL         NOT NULL,
            current_amount   REAL         NOT NULL DEFAULT 0,
            future_amount    REAL         NOT NULL,
            interest_rate    REAL,
            start_date       DATE         NOT NULL,
            maturity_date    DATE,
            status           VARCHAR      NOT NULL DEFAULT 'active',
            notes            TEXT,
            linked_project_id INTEGER     REFERENCES financial_projects(id),
            created_at       DATETIME,
            completed_at     DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS import_jobs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            filename         VARCHAR(255) NOT NULL,
            file_path        VARCHAR(500) NOT NULL,
            image_hash       VARCHAR(64)  NOT NULL UNIQUE,
            source_hint      VARCHAR,
            detected_source  VARCHAR,
            status           VARCHAR      NOT NULL DEFAULT 'pending',
            error_message    TEXT,
            transaction_count INTEGER     NOT NULL DEFAULT 0,
            created_at       DATETIME,
            processed_at     DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS transactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            date             DATE         NOT NULL,
            amount           REAL         NOT NULL,
            type             VARCHAR      NOT NULL,
            category_id      INTEGER      NOT NULL REFERENCES categories(id),
            description      TEXT,
            payment_method   VARCHAR(50)  DEFAULT 'cash',
            is_savings_related BOOLEAN    NOT NULL DEFAULT 0,
            savings_bundle_id  INTEGER    REFERENCES savings_bundles(id),
            project_id         INTEGER    REFERENCES financial_projects(id),
            import_job_id      INTEGER    REFERENCES import_jobs(id),
            created_at       DATETIME,
            updated_at       DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS project_payments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL REFERENCES financial_projects(id),
            due_date    DATE,
            amount      REAL    NOT NULL,
            status      VARCHAR NOT NULL DEFAULT 'pending',
            notes       TEXT,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            transaction_id INTEGER REFERENCES transactions(id),
            created_at  DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS other_assets (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                VARCHAR(200) NOT NULL,
            asset_type          VARCHAR      NOT NULL,
            symbol              VARCHAR(20),
            quantity            REAL         NOT NULL,
            unit                VARCHAR(50)  NOT NULL,
            purchase_price_vnd  REAL         NOT NULL,
            current_value_vnd   REAL         NOT NULL,
            notes               TEXT,
            acquired_date       DATE,
            created_at          DATETIME,
            updated_at          DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS transaction_templates (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           VARCHAR(200) NOT NULL,
            amount         REAL         NOT NULL,
            type           VARCHAR      NOT NULL,
            category_id    INTEGER      NOT NULL REFERENCES categories(id),
            description    TEXT,
            payment_method VARCHAR(50)  DEFAULT 'cash',
            is_active      BOOLEAN      NOT NULL DEFAULT 1,
            created_at     DATETIME,
            updated_at     DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      VARCHAR(200) NOT NULL,
            content    TEXT,
            type       VARCHAR(50),
            created_at DATETIME,
            updated_at DATETIME
        )
    """)
    )
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS budget_allocations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            year_month  VARCHAR(7) NOT NULL,
            amount      REAL       NOT NULL,
            created_at  DATETIME,
            updated_at  DATETIME,
            UNIQUE (category_id, year_month)
        )
    """)
    )

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
