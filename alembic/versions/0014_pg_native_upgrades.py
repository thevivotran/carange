"""feat: PostgreSQL-native upgrades — NUMERIC, TIMESTAMPTZ, JSONB, triggers, indexes

All changes are guarded by dialect so the migration is a no-op on SQLite
(test suite uses sqlite:///:memory:).

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-05
"""

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op

# ── Money columns to convert to NUMERIC(18, 0) ───────────────────────────────
_NUMERIC_COLS = [
    ("transactions", "amount"),
    ("savings_bundles", "initial_deposit"),
    ("savings_bundles", "current_amount"),
    ("savings_bundles", "future_amount"),
    ("financial_projects", "target_amount"),
    ("financial_projects", "current_amount"),
    ("project_payments", "amount"),
    ("project_contributions", "amount"),
    ("other_assets", "purchase_price_vnd"),
    ("other_assets", "current_value_vnd"),
    ("transaction_templates", "amount"),
    ("budget_allocations", "amount"),
]

# ── DateTime columns to convert to TIMESTAMPTZ ────────────────────────────────
_TIMESTAMPTZ_COLS = [
    ("transactions", ["created_at", "updated_at", "deleted_at"]),
    ("categories", ["created_at"]),
    ("savings_bundles", ["created_at", "completed_at", "deleted_at"]),
    ("financial_projects", ["created_at", "completed_at", "deleted_at"]),
    ("project_payments", ["created_at"]),
    ("other_assets", ["created_at", "updated_at"]),
    ("transaction_templates", ["created_at", "updated_at"]),
    ("budget_allocations", ["created_at", "updated_at"]),
    ("transaction_rules", ["created_at", "updated_at", "last_matched_at"]),
    ("payees", ["created_at", "updated_at"]),
    ("email_ingest_log", ["received_at", "processed_at", "created_at"]),
    ("import_jobs", ["created_at", "processed_at"]),
    ("notes", ["created_at", "updated_at"]),
    ("settings", ["updated_at"]),
    ("transaction_audit_logs", ["changed_at"]),
    ("period_rollups", ["computed_at"]),
    ("narratives", ["generated_at"]),
]

# ── Tables with updated_at triggers ──────────────────────────────────────────
_UPDATED_AT_TABLES = [
    "transactions",
    "other_assets",
    "transaction_templates",
    "budget_allocations",
    "transaction_rules",
    "payees",
    "notes",
    "settings",
    "goals",
]


def upgrade() -> None:
    bind = op.get_bind()

    # ── 0. period_rollups table (cross-dialect: cache sentinel for cross-pod invalidation) ─
    inspector = sa.inspect(bind)
    if "period_rollups" not in inspector.get_table_names():
        op.create_table(
            "period_rollups",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("horizon", sa.String(length=10), nullable=False),
            sa.Column("period_key", sa.String(length=20), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("horizon", "period_key", name="uq_period_rollups_horizon_key"),
        )
        op.create_index(op.f("ix_period_rollups_id"), "period_rollups", ["id"], unique=False)

    if bind.dialect.name != "postgresql":
        return

    # ── 1. pg_trgm extension ─────────────────────────────────────────────────
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    # ── 2. NUMERIC(18,0) for money columns ───────────────────────────────────
    inspector = sa.inspect(bind)
    for table, col in _NUMERIC_COLS:
        existing_tables = inspector.get_table_names()
        if table not in existing_tables:
            continue
        op.alter_column(
            table,
            col,
            type_=sa.Numeric(18, 0),
            postgresql_using=f"{col}::numeric(18,0)",
        )

    # ── 3. TIMESTAMPTZ for datetime columns ───────────────────────────────────
    for table, cols in _TIMESTAMPTZ_COLS:
        if table not in inspector.get_table_names():
            continue
        existing_cols = {c["name"]: c for c in inspector.get_columns(table)}
        for col in cols:
            if col not in existing_cols:
                continue
            col_type = existing_cols[col]["type"]
            if getattr(col_type, "timezone", False):
                continue  # already TIMESTAMPTZ — skip
            # text columns came from SQLite migration; cast directly
            is_text = isinstance(col_type, (sa.String, sa.Text)) or str(col_type) in ("TEXT", "VARCHAR")
            using = f"{col}::timestamptz" if is_text else f"{col} AT TIME ZONE 'UTC'"
            op.alter_column(
                table,
                col,
                type_=sa.TIMESTAMP(timezone=True),
                postgresql_using=using,
            )

    # ── 4. JSONB for JSON text columns ────────────────────────────────────────
    # Map of columns that carry a text DEFAULT which must be re-expressed as JSONB
    _jsonb_defaults: dict[tuple, str] = {
        ("transaction_rules", "action_json"): "'{}'::jsonb",
    }
    for table, col, nullable in [
        ("transaction_rules", "action_json", False),
        ("payees", "alias_patterns", True),
        ("period_rollups", "payload_json", False),
    ]:
        if table not in inspector.get_table_names():
            continue
        # Drop any existing default before type change; PostgreSQL refuses to
        # auto-cast a text default to jsonb.
        bind.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {col} DROP DEFAULT"))
        op.alter_column(
            table,
            col,
            type_=sa.dialects.postgresql.JSONB(),
            postgresql_using=f"CASE WHEN {col} IS NULL THEN NULL ELSE {col}::jsonb END",
            nullable=nullable,
        )
        new_default = _jsonb_defaults.get((table, col))
        if new_default:
            bind.execute(sa.text(f"ALTER TABLE {table} ALTER COLUMN {col} SET DEFAULT {new_default}"))

    # ── 5. updated_at trigger function ────────────────────────────────────────
    bind.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION trigger_set_updated_at()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$
    """)
    )

    for table in _UPDATED_AT_TABLES:
        if table not in inspector.get_table_names():
            continue
        existing_col_names = {c["name"] for c in inspector.get_columns(table)}
        if "updated_at" not in existing_col_names:
            continue
        trigger_name = f"trg_{table}_updated_at"
        bind.execute(
            sa.text(f"""
            DROP TRIGGER IF EXISTS {trigger_name} ON {table};
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at()
        """)
        )

    # ── 6. Partial indexes on soft-deleted tables ─────────────────────────────
    existing_idx = {
        idx["name"]
        for t in ["savings_bundles", "financial_projects"]
        if t in inspector.get_table_names()
        for idx in inspector.get_indexes(t)
    }

    if "ix_savings_bundles_active" not in existing_idx:
        op.create_index(
            "ix_savings_bundles_active",
            "savings_bundles",
            ["status", "maturity_date"],
            postgresql_where=sa.text("deleted_at IS NULL"),
        )

    if "ix_financial_projects_active" not in existing_idx:
        op.create_index(
            "ix_financial_projects_active",
            "financial_projects",
            ["status"],
            postgresql_where=sa.text("deleted_at IS NULL"),
        )

    # ── 7. GIN trgm index on transactions.description ────────────────────────
    tx_idx = {idx["name"] for idx in inspector.get_indexes("transactions")}
    if "ix_transactions_description_trgm" not in tx_idx:
        bind.execute(
            sa.text("""
            CREATE INDEX ix_transactions_description_trgm
            ON transactions USING gin(description gin_trgm_ops)
            WHERE deleted_at IS NULL
        """)
        )

    # ── 8. CHECK constraints for enum-like VARCHAR columns ────────────────────
    bind.execute(
        sa.text("""
        ALTER TABLE transactions
        DROP CONSTRAINT IF EXISTS chk_tx_type,
        ADD CONSTRAINT chk_tx_type CHECK (type IN ('expense', 'income'))
    """)
    )
    bind.execute(
        sa.text("""
        ALTER TABLE categories
        DROP CONSTRAINT IF EXISTS chk_cat_type,
        ADD CONSTRAINT chk_cat_type CHECK (type IN ('expense', 'income'))
    """)
    )
    bind.execute(
        sa.text("""
        ALTER TABLE transactions
        DROP CONSTRAINT IF EXISTS chk_tx_source,
        ADD CONSTRAINT chk_tx_source
        CHECK (source IN ('manual','csv','ocr','email','template') OR source IS NULL)
    """)
    )


def downgrade() -> None:
    bind = op.get_bind()

    # ── 0. period_rollups table (cross-dialect) ──────────────────────────────
    inspector = sa.inspect(bind)
    if "period_rollups" in inspector.get_table_names():
        op.drop_index(op.f("ix_period_rollups_id"), table_name="period_rollups")
        op.drop_table("period_rollups")

    if bind.dialect.name != "postgresql":
        return

    # Drop CHECK constraints
    bind.execute(sa.text("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS chk_tx_type"))
    bind.execute(sa.text("ALTER TABLE categories DROP CONSTRAINT IF EXISTS chk_cat_type"))
    bind.execute(sa.text("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS chk_tx_source"))

    # Drop trgm index
    op.drop_index("ix_transactions_description_trgm", table_name="transactions", if_exists=True)

    # Drop partial indexes
    op.drop_index("ix_financial_projects_active", table_name="financial_projects", if_exists=True)
    op.drop_index("ix_savings_bundles_active", table_name="savings_bundles", if_exists=True)

    # Drop triggers
    inspector = sa.inspect(bind)
    for table in _UPDATED_AT_TABLES:
        if table not in inspector.get_table_names():
            continue
        bind.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}"))
    bind.execute(sa.text("DROP FUNCTION IF EXISTS trigger_set_updated_at()"))

    # Revert JSONB → TEXT
    for table, col, nullable in [
        ("transaction_rules", "action_json", False),
        ("payees", "alias_patterns", True),
        ("period_rollups", "payload_json", False),
    ]:
        if table not in inspector.get_table_names():
            continue
        op.alter_column(table, col, type_=sa.Text(), nullable=nullable)

    # Revert TIMESTAMPTZ → TIMESTAMP
    for table, cols in _TIMESTAMPTZ_COLS:
        if table not in inspector.get_table_names():
            continue
        existing_col_names = {c["name"] for c in inspector.get_columns(table)}
        for col in cols:
            if col in existing_col_names:
                op.alter_column(table, col, type_=sa.DateTime())

    # Revert NUMERIC → FLOAT
    for table, col in _NUMERIC_COLS:
        if table not in inspector.get_table_names():
            continue
        op.alter_column(table, col, type_=sa.Float(), postgresql_using=f"{col}::float8")
