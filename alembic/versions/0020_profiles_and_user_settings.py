"""feat(profiles): household profile picker with per-user UI preferences

Adds:
  • users: passwordless household profiles (Netflix-style picker; Tailscale is
    the security boundary). name + avatar color + last_seen_at.
  • user_settings: per-profile K-V preferences (nav_items, dashboard_sections),
    mirroring the global settings table which lives on as the household default
    for new profiles.

No data backfill: profiles are created via the picker UI, which seeds each new
profile's preferences from the existing global nav_layout / dashboard_layout.

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-10
"""

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("color", sa.String(20), nullable=False, server_default="#2563EB"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("name", name="uq_users_name"),
    )
    op.create_table(
        "user_settings",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
    op.drop_table("users")
