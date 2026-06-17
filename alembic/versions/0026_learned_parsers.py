"""feat: learned_parsers table

Creates the `learned_parsers` table used by the AI fallback loop to store
dynamically-generated parsers for unknown billing formats.

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learned_parsers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("detection_keywords", sa.JSON(), nullable=False),
        sa.Column("extraction_script", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("NOW()")),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_name"),
    )
    op.create_index("ix_learned_parsers_id", "learned_parsers", ["id"])


def downgrade() -> None:
    op.drop_table("learned_parsers")
