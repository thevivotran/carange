"""
Migration 001: Add import_jobs table and extend transactions for OCR import feature.

Run with:
    cd carange_app/carange
    .venv/bin/python migrations/001_add_import_jobs.py
"""

import os
import sqlite3

DATABASE_PATH = os.getenv("DATABASE_PATH", "carange.db")


def column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def run(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    # import_jobs is a new table — SQLAlchemy create_all handles it,
    # but we create it here too so this script is self-contained.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS import_jobs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            filename         VARCHAR(255) NOT NULL,
            file_path        VARCHAR(500) NOT NULL,
            image_hash       VARCHAR(64)  NOT NULL UNIQUE,
            source_hint      VARCHAR(20),
            detected_source  VARCHAR(20),
            status           VARCHAR(20)  NOT NULL DEFAULT 'pending',
            error_message    TEXT,
            transaction_count INTEGER DEFAULT 0,
            created_at       DATETIME,
            processed_at     DATETIME
        )
    """)

    # New columns on transactions — ALTER TABLE ADD COLUMN is idempotent-guarded.
    new_columns = [
        ("import_job_id", "INTEGER REFERENCES import_jobs(id)"),
        ("confidence_score", "REAL"),
        ("needs_review", "BOOLEAN NOT NULL DEFAULT 0"),
    ]
    for col_name, col_def in new_columns:
        if not column_exists(cur, "transactions", col_name):
            cur.execute(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_def}")
            print(f"  + transactions.{col_name}")
        else:
            print(f"  ~ transactions.{col_name} already exists, skipped")

    conn.commit()
    print("Migration 001 complete.")


if __name__ == "__main__":
    with sqlite3.connect(DATABASE_PATH) as conn:
        run(conn)
