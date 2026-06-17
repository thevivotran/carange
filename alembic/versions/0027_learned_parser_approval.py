"""feat: add is_approved column to learned_parsers

Adds human-approval gate so AI-generated parsers are pending review
before they can be executed.

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("learned_parsers") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_approved",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("learned_parsers") as batch_op:
        batch_op.drop_column("is_approved")
