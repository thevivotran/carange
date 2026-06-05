"""Budget domain business logic with SQL-based cumulative computation."""

import calendar
from collections import defaultdict
from datetime import date as _date

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.database import BudgetAllocation, Category, Transaction, TransactionType


def _is_postgresql(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def get_baseline_month(db: Session) -> str:
    """Return the earliest allocation month, or current month if none exist."""
    earliest = db.query(func.min(BudgetAllocation.year_month)).scalar()
    if earliest:
        return earliest
    today = _date.today()
    return f"{today.year:04d}-{today.month:02d}"


def months_range(from_ym: str, to_ym: str) -> list[str]:
    """Return list of YYYY-MM strings from from_ym up to and including to_ym."""
    result = []
    y, m = int(from_ym[:4]), int(from_ym[5:])
    ey, em = int(to_ym[:4]), int(to_ym[5:])
    while (y, m) <= (ey, em):
        result.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return result


def end_of_month(year_month: str) -> str:
    y, m = int(year_month[:4]), int(year_month[5:])
    last = calendar.monthrange(y, m)[1]
    return f"{year_month}-{last:02d}"


def compute_budget_rows(db: Session, year_month: str) -> list[dict]:
    """Compute budget tracking rows for a given month.

    The step-function allocation carry-forward is resolved via a correlated SQL
    subquery per (category, month), avoiding a Python month-by-month loop.
    Spending queries use ORM to avoid raw SQL type-storage assumptions.
    """
    baseline = get_baseline_month(db)
    if year_month < baseline:
        return []

    all_months = months_range(baseline, year_month)

    records = (
        db.query(BudgetAllocation)
        .filter(BudgetAllocation.year_month <= year_month)
        .order_by(BudgetAllocation.category_id, BudgetAllocation.year_month)
        .all()
    )

    alloc_by_cat: dict[int, list] = defaultdict(list)
    for r in records:
        alloc_by_cat[r.category_id].append((r.year_month, float(r.amount), r.id))

    active_cat_ids = list(alloc_by_cat.keys())
    if not active_cat_ids:
        return []

    cats = {c.id: c for c in db.query(Category).filter(Category.id.in_(active_cat_ids)).all()}

    end_date_str = end_of_month(year_month)
    month_start_str = f"{year_month}-01"
    month_end_str = end_of_month(year_month)

    # Per-category first-allocation date: spending before a category's own budget
    # window must not count as rollover, even when the global baseline is earlier.
    # alloc_by_cat entries are in year_month order (ORDER BY in the records query).
    cat_first_alloc_start = {cat_id: f"{allocs[0][0]}-01" for cat_id, allocs in alloc_by_cat.items()}

    # ── SQL: cumulative spending using per-category start dates ───────────────
    # UNION ALL instead of VALUES (...) so the CTE is portable across SQLite and PostgreSQL.
    # Pass start dates as Python date objects so SQLAlchemy binds them correctly on both
    # dialects (no manual CAST needed — sqlite3 uses isoformat text, psycopg2 uses DATE).
    params: dict = {"end_date": end_date_str}
    union_rows: list[str] = []
    for i, (cat_id, start) in enumerate(cat_first_alloc_start.items()):
        params[f"cid_{i}"] = cat_id
        params[f"sd_{i}"] = _date.fromisoformat(start)
        union_rows.append(f"SELECT :cid_{i}, :sd_{i}")
    cat_starts_sql = " UNION ALL ".join(union_rows)
    cumulative_spent_rows = db.execute(
        text(f"""
        WITH cat_starts(cat_id, start_date) AS ({cat_starts_sql})
        SELECT t.category_id, SUM(t.amount)
        FROM transactions t
        JOIN cat_starts cs ON t.category_id = cs.cat_id
        WHERE t.type = 'expense'
          AND t.deleted_at IS NULL
          AND t.date >= cs.start_date
          AND t.date <= :end_date
        GROUP BY t.category_id
        """),
        params,
    ).fetchall()
    cumulative_spent_map = {r[0]: float(r[1] or 0) for r in cumulative_spent_rows}

    # ── ORM: this-month spending ───────────────────────────────────────────────
    this_month_rows = (
        db.query(Transaction.category_id, func.sum(Transaction.amount).label("total"))
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.category_id.in_(active_cat_ids),
            Transaction.date >= month_start_str,
            Transaction.date <= month_end_str,
            Transaction.deleted_at.is_(None),
        )
        .group_by(Transaction.category_id)
        .all()
    )
    this_month_map = {r[0]: float(r[1] or 0) for r in this_month_rows}

    # ── Cumulative allocated: step-function carry-forward via SQL CTE ─────────
    if all_months:
        if _is_postgresql(db):
            # generate_series replaces the UNION ALL month list on PostgreSQL
            start_gs = f"{all_months[0]}-01"
            end_gs = f"{all_months[-1]}-01"
            cumulative_alloc_rows = db.execute(
                text("""
                WITH month_series(ym) AS (
                    SELECT to_char(gs::date, 'YYYY-MM')
                    FROM generate_series(
                        CAST(:start_gs AS date), CAST(:end_gs AS date), '1 month'::interval
                    ) AS gs
                ),
                resolved AS (
                    SELECT
                        cats.cat_id,
                        ms.ym,
                        (
                            SELECT amount
                            FROM budget_allocations ba
                            WHERE ba.category_id = cats.cat_id
                              AND ba.year_month <= ms.ym
                            ORDER BY ba.year_month DESC
                            LIMIT 1
                        ) AS applicable_amount
                    FROM (SELECT DISTINCT category_id AS cat_id FROM budget_allocations
                          WHERE year_month <= :year_month) AS cats
                    CROSS JOIN month_series ms
                )
                SELECT cat_id, SUM(COALESCE(applicable_amount, 0)) AS cumulative_allocated
                FROM resolved
                GROUP BY cat_id
                """),
                {"year_month": year_month, "start_gs": start_gs, "end_gs": end_gs},
            ).fetchall()
        else:
            # SQLite fallback: explicit UNION ALL month list
            month_selects = " UNION ALL ".join(f"SELECT '{m}'" for m in all_months)
            cumulative_alloc_rows = db.execute(
                text(f"""
                WITH month_series(ym) AS ({month_selects}),
                resolved AS (
                    SELECT
                        cats.cat_id,
                        ms.ym,
                        (
                            SELECT amount
                            FROM budget_allocations ba
                            WHERE ba.category_id = cats.cat_id
                              AND ba.year_month <= ms.ym
                            ORDER BY ba.year_month DESC
                            LIMIT 1
                        ) AS applicable_amount
                    FROM (SELECT DISTINCT category_id AS cat_id FROM budget_allocations
                          WHERE year_month <= :year_month) AS cats
                    CROSS JOIN month_series ms
                )
                SELECT cat_id, SUM(COALESCE(applicable_amount, 0)) AS cumulative_allocated
                FROM resolved
                GROUP BY cat_id
                """),
                {"year_month": year_month},
            ).fetchall()
        cumulative_alloc_map = {r[0]: float(r[1] or 0) for r in cumulative_alloc_rows}

    result = []
    for cat_id, allocs in alloc_by_cat.items():
        cat = cats.get(cat_id)
        if not cat:
            continue

        current_alloc_amount = 0.0
        current_alloc_id = None
        current_alloc_ym = None
        for ym, amount, alloc_id in allocs:
            if ym <= year_month:
                current_alloc_ym = ym
                current_alloc_amount = amount
                current_alloc_id = alloc_id

        cumulative_allocated = cumulative_alloc_map.get(cat_id, 0.0)
        cumulative_spent = cumulative_spent_map.get(cat_id, 0)
        this_month_spent = this_month_map.get(cat_id, 0)
        available_balance = cumulative_allocated - cumulative_spent
        usage_pct = (this_month_spent / current_alloc_amount * 100) if current_alloc_amount else 0
        # % of effective budget (monthly allocation + rollover) used THIS month
        effective_budget = available_balance + this_month_spent
        cumulative_pct = (this_month_spent / effective_budget * 100) if effective_budget else 0

        result.append(
            {
                "category_id": cat_id,
                "category_name": cat.name,
                "category_color": cat.color,
                "monthly_allocation": current_alloc_amount,
                "cumulative_allocated": cumulative_allocated,
                "cumulative_spent": float(cumulative_spent),
                "this_month_spent": float(this_month_spent),
                "available_balance": available_balance,
                "usage_pct": round(usage_pct, 1),
                "cumulative_pct": round(cumulative_pct, 1),
                "allocation_id": current_alloc_id,
                "has_own_allocation": current_alloc_ym == year_month,
            }
        )

    result.sort(key=lambda r: r["available_balance"])
    return result
