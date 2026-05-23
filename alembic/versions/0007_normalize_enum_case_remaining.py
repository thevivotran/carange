"""Normalize remaining enum columns to lowercase missed by 0006

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-23

Tables missed by 0006:
  - financial_projects: type, priority, status
  - project_payments:   status
  - other_assets:       asset_type
  - transaction_templates: type
  - import_jobs:        detected_source, source_hint, status
  - transaction_audit_logs: field_name
"""

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

import sqlalchemy as sa
from alembic import op


def upgrade() -> None:
    conn = op.get_bind()

    t = sa.text
    conn.execute(t("UPDATE financial_projects SET type = LOWER(type) WHERE type != LOWER(type)"))
    conn.execute(t("UPDATE financial_projects SET priority = LOWER(priority) WHERE priority != LOWER(priority)"))
    conn.execute(t("UPDATE financial_projects SET status = LOWER(status) WHERE status != LOWER(status)"))

    conn.execute(t("UPDATE project_payments SET status = LOWER(status) WHERE status != LOWER(status)"))

    conn.execute(t("UPDATE other_assets SET asset_type = LOWER(asset_type) WHERE asset_type != LOWER(asset_type)"))

    conn.execute(t("UPDATE transaction_templates SET type = LOWER(type) WHERE type != LOWER(type)"))

    conn.execute(
        t(
            "UPDATE import_jobs SET source_hint = LOWER(source_hint)"
            " WHERE source_hint IS NOT NULL AND source_hint != LOWER(source_hint)"
        )
    )
    conn.execute(
        t(
            "UPDATE import_jobs SET detected_source = LOWER(detected_source)"
            " WHERE detected_source IS NOT NULL AND detected_source != LOWER(detected_source)"
        )
    )
    conn.execute(t("UPDATE import_jobs SET status = LOWER(status) WHERE status != LOWER(status)"))

    conn.execute(
        t("UPDATE transaction_audit_logs SET field_name = LOWER(field_name) WHERE field_name != LOWER(field_name)")
    )


def downgrade() -> None:
    pass
