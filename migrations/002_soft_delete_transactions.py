"""
Migration 002: Add deleted_at to transactions for soft-delete support.

Run with:
    cd carange_app/carange
    .venv/bin/python migrations/002_soft_delete_transactions.py
"""

import os
import sqlite3

DATABASE_PATH = os.getenv("DATABASE_PATH", "carange.db")


def column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def run(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    if not column_exists(cur, "transactions", "deleted_at"):
        cur.execute("ALTER TABLE transactions ADD COLUMN deleted_at DATETIME")
        print("  + transactions.deleted_at")
    else:
        print("  ~ transactions.deleted_at already exists, skipped")

    conn.commit()
    print("Migration 002 complete.")


if __name__ == "__main__":
    with sqlite3.connect(DATABASE_PATH) as conn:
        run(conn)
