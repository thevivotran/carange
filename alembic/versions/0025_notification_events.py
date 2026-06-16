"""feat: notification_events table + PG notify trigger

Creates the cross-dialect `notification_events` table used by the event-driven
telegram notification system, plus a PostgreSQL-only pg_notify trigger that
wakes the notify worker on every INSERT.

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_events_status_retry",
        "notification_events",
        ["status", "retry_after"],
    )

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        sa.text("""
        CREATE OR REPLACE FUNCTION notify_telegram_worker()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_notify('telegram_notifications', NEW.id::text);
            RETURN NEW;
        END; $$;

        CREATE TRIGGER trg_notification_events_notify
        AFTER INSERT ON notification_events
        FOR EACH ROW EXECUTE FUNCTION notify_telegram_worker();
    """)
    )


def downgrade() -> None:
    op.drop_table("notification_events")
