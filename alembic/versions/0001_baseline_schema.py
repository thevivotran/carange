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
from sqlalchemy import inspect as sa_inspect


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    existing_tables = set(inspector.get_table_names())

    # ── Fresh-install bootstrap ───────────────────────────────────────────────
    # These tables existed before Alembic was introduced. On a production DB they
    # already exist; on a fresh install they must be created first so that
    # subsequent ALTER TABLE statements in this and later migrations can run.
    # Column sets are intentionally minimal — later migrations add the rest.
    if "categories" not in existing_tables:
        op.create_table(
            "categories",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("color", sa.String(7), server_default="#3B82F6"),
            sa.Column("icon", sa.String(50), server_default="circle"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime()),
        )

    if "financial_projects" not in existing_tables:
        op.create_table(
            "financial_projects",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("target_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("current_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("priority", sa.String(), nullable=False, server_default="medium"),
            sa.Column("status", sa.String(), nullable=False, server_default="planning"),
            sa.Column("deadline", sa.Date()),
            sa.Column("default_category_id", sa.Integer(), sa.ForeignKey("categories.id")),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("completed_at", sa.DateTime()),
        )

    if "savings_bundles" not in existing_tables:
        op.create_table(
            "savings_bundles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("bank_name", sa.String(100), nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("initial_deposit", sa.Float(), nullable=False),
            sa.Column("current_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("future_amount", sa.Float(), nullable=False),
            sa.Column("interest_rate", sa.Float()),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("maturity_date", sa.Date()),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("notes", sa.Text()),
            sa.Column("linked_project_id", sa.Integer(), sa.ForeignKey("financial_projects.id")),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("completed_at", sa.DateTime()),
        )

    if "import_jobs" not in existing_tables:
        op.create_table(
            "import_jobs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("filename", sa.String(255), nullable=False),
            sa.Column("file_path", sa.String(500), nullable=False),
            sa.Column("image_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("source_hint", sa.String()),
            sa.Column("detected_source", sa.String()),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("error_message", sa.Text()),
            sa.Column("transaction_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("processed_at", sa.DateTime()),
        )

    if "transactions" not in existing_tables:
        op.create_table(
            "transactions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("payment_method", sa.String(50), server_default="cash"),
            sa.Column("is_savings_related", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("savings_bundle_id", sa.Integer(), sa.ForeignKey("savings_bundles.id")),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("financial_projects.id")),
            sa.Column("import_job_id", sa.Integer(), sa.ForeignKey("import_jobs.id")),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        )

    if "project_payments" not in existing_tables:
        op.create_table(
            "project_payments",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("financial_projects.id"), nullable=False),
            sa.Column("due_date", sa.Date()),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("notes", sa.Text()),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transactions.id")),
            sa.Column("created_at", sa.DateTime()),
        )

    if "other_assets" not in existing_tables:
        op.create_table(
            "other_assets",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("asset_type", sa.String(), nullable=False),
            sa.Column("symbol", sa.String(20)),
            sa.Column("quantity", sa.Float(), nullable=False),
            sa.Column("unit", sa.String(50), nullable=False),
            sa.Column("purchase_price_vnd", sa.Float(), nullable=False),
            sa.Column("current_value_vnd", sa.Float(), nullable=False),
            sa.Column("notes", sa.Text()),
            sa.Column("acquired_date", sa.Date()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        )

    if "transaction_templates" not in existing_tables:
        op.create_table(
            "transaction_templates",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("type", sa.String(), nullable=False),
            sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
            sa.Column("description", sa.Text()),
            sa.Column("payment_method", sa.String(50), server_default="cash"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        )

    if "notes" not in existing_tables:
        op.create_table(
            "notes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("content", sa.Text()),
            sa.Column("type", sa.String(50)),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        )

    if "budget_allocations" not in existing_tables:
        op.create_table(
            "budget_allocations",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=False),
            sa.Column("year_month", sa.String(7), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
            sa.UniqueConstraint("category_id", "year_month"),
        )

    # ── transactions: ad-hoc columns added over sprints 1-2 ─────────────────
    tx_cols = {col["name"] for col in inspector.get_columns("transactions")}

    cols_to_add = [
        ("is_advance", sa.Boolean(), {"server_default": sa.false()}),
        ("advance_settled", sa.Boolean(), {"server_default": sa.false()}),
        ("source", sa.String(30), {"server_default": "manual"}),
        ("import_job_id", sa.Integer(), {}),
        ("confidence_score", sa.Float(), {}),
        ("needs_review", sa.Boolean(), {"nullable": False, "server_default": sa.false()}),
        ("deleted_at", sa.DateTime(), {}),
    ]
    for col_name, col_type, col_kwargs in cols_to_add:
        if col_name not in tx_cols:
            op.add_column("transactions", sa.Column(col_name, col_type, **col_kwargs))

    # ── transaction_audit_logs (may not exist on fresh installs) ────────────
    if "transaction_audit_logs" not in existing_tables:
        op.create_table(
            "transaction_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transactions.id"), nullable=False),
            sa.Column("changed_at", sa.DateTime(), nullable=False),
            sa.Column("field_name", sa.String(100), nullable=False),
            sa.Column("old_value", sa.Text()),
            sa.Column("new_value", sa.Text()),
        )

    # ── indexes ──────────────────────────────────────────────────────────────
    existing_idx = {idx["name"] for idx in inspector.get_indexes("transactions")}
    if "ix_transactions_type_date" not in existing_idx:
        op.create_index("ix_transactions_type_date", "transactions", ["type", "date"])
    if "ix_transactions_category_id" not in existing_idx:
        op.create_index("ix_transactions_category_id", "transactions", ["category_id"])

    # ── category name normalisation ──────────────────────────────────────────
    conn.execute(sa.text("UPDATE categories SET name = 'Chi phí khác' WHERE name = 'Khác' AND type = 'EXPENSE'"))
    conn.execute(sa.text("UPDATE categories SET name = 'Thu nhập khác' WHERE name = 'Khác' AND type = 'INCOME'"))


def downgrade() -> None:
    pass  # baseline is irreversible
