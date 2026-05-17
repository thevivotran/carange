"""
Migration 002: Rename ambiguous "Khác" categories to type-specific names.

  expense "Khác" → "Chi phí khác"
  income  "Khác" → "Thu nhập khác"

Run locally:
    cd carange_app/carange
    .venv/bin/python migrations/002_rename_khac_categories.py

Run on k3s:
    kubectl exec -n carange deploy/carange -- \
        python migrations/002_rename_khac_categories.py
"""

import os
import sqlite3

DATABASE_PATH = os.getenv("DATABASE_PATH", "carange.db")


def run(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()

    renames = [
        ("Khác", "expense", "Chi phí khác"),
        ("Khác", "income",  "Thu nhập khác"),
    ]

    for old_name, cat_type, new_name in renames:
        cur.execute(
            "SELECT id, name FROM categories WHERE name = ? AND type = ?",
            (old_name, cat_type),
        )
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, row[0]))
            print(f"  [{cat_type}] id={row[0]}: '{old_name}' → '{new_name}'")
        else:
            cur.execute(
                "SELECT id, name FROM categories WHERE name = ? AND type = ?",
                (new_name, cat_type),
            )
            already = cur.fetchone()
            if already:
                print(f"  [{cat_type}] '{new_name}' already exists (id={already[0]}), skipped")
            else:
                print(f"  [{cat_type}] WARNING: no category named '{old_name}' found — skipped")

    conn.commit()
    print("Migration 002 complete.")


if __name__ == "__main__":
    with sqlite3.connect(DATABASE_PATH) as conn:
        run(conn)
