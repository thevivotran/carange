"""feat(email-worker): UID-cursor ingestion, raw email storage, DB-backed learned patterns

Email worker v2:
  • email_ingest_log: add raw_email (zlib-compressed RFC 2822 source for retries/replay),
    raw_size (UI availability check without loading the blob), parser_name
  • imap_folder_state: per (account, folder) UID high-water mark — replaces the fragile
    UNSEEN-flag queue with UIDVALIDITY-aware cursor tracking
  • learned_patterns: moves LLM-learned regex patterns from an ephemeral JSON file inside
    the container into the shared database (adds failure_count for pattern invalidation)

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-10
"""

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # ── email_ingest_log additions ────────────────────────────────────────────
    op.add_column("email_ingest_log", sa.Column("raw_email", sa.LargeBinary(), nullable=True))
    op.add_column("email_ingest_log", sa.Column("raw_size", sa.Integer(), nullable=True))
    op.add_column("email_ingest_log", sa.Column("parser_name", sa.String(100), nullable=True))

    # ── imap_folder_state ─────────────────────────────────────────────────────
    op.create_table(
        "imap_folder_state",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("account", sa.String(200), nullable=False),
        sa.Column("folder", sa.String(200), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_uid", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account", "folder", name="uq_imap_folder_state_account_folder"),
    )

    # ── learned_patterns ──────────────────────────────────────────────────────
    op.create_table(
        "learned_patterns",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("domain", sa.String(200), nullable=False),
        sa.Column("patterns", sa.Text(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_learned_patterns_domain", "learned_patterns", ["domain"], unique=True)

    if not is_pg:
        return

    for tbl, col in [
        ("imap_folder_state", "updated_at"),
        ("learned_patterns", "generated_at"),
        ("learned_patterns", "updated_at"),
    ]:
        op.execute(sa.text(f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE TIMESTAMPTZ"))


def downgrade() -> None:
    op.drop_index("ix_learned_patterns_domain", table_name="learned_patterns")
    op.drop_table("learned_patterns")
    op.drop_table("imap_folder_state")
    op.drop_column("email_ingest_log", "parser_name")
    op.drop_column("email_ingest_log", "raw_size")
    op.drop_column("email_ingest_log", "raw_email")
