"""One-off migration: create the new indexes added in A1 on an existing carange.db.

Run once against any DB that predates the index additions:
    cd carange_app/carange && .venv/bin/python utils/add_indexes.py
"""

import sqlite3
import sys
import os

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "carange.db"))

INDEXES = [
    ("ix_transactions_deleted_at", "CREATE INDEX IF NOT EXISTS ix_transactions_deleted_at ON transactions (deleted_at)"),
    ("ix_transactions_import_job_id", "CREATE INDEX IF NOT EXISTS ix_transactions_import_job_id ON transactions (import_job_id)"),
    ("ix_transactions_needs_review", "CREATE INDEX IF NOT EXISTS ix_transactions_needs_review ON transactions (needs_review)"),
    ("ix_transactions_is_savings_related", "CREATE INDEX IF NOT EXISTS ix_transactions_is_savings_related ON transactions (is_savings_related)"),
    ("ix_savings_bundles_status", "CREATE INDEX IF NOT EXISTS ix_savings_bundles_status ON savings_bundles (status)"),
    ("ix_financial_projects_status", "CREATE INDEX IF NOT EXISTS ix_financial_projects_status ON financial_projects (status)"),
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for name, sql in INDEXES:
        cur.execute(sql)
        print(f"  OK  {name}")
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
