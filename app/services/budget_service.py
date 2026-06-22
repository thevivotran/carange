"""Budget domain business logic with SQL-based cumulative computation."""

from collections import defaultdict
from datetime import date as _date

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.database import BudgetAllocation, Category, Transaction, TransactionType


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


def compute_budget_rows(db: Session, year_month: str, day: int | None = None) -> list[dict]:
    """Compute budget tracking rows for a given month.

    The step-function allocation carry-forward is resolved via a SQL window
    function (LEAD) over allocation spans, avoiding a Python month-by-month loop.
    Spending queries use ORM to avoid raw SQL type-storage assumptions.

    ``day`` is the fiscal month-start day; callers that already loaded it can
    pass it to avoid a redundant settings query.
    """
    from app.services.fiscal_period import fiscal_window, get_month_start_day

    if day is None:
        day = get_month_start_day(db)

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

    month_start, month_end = fiscal_window(year_month, day)

    # Per-category first-allocation date: spending before a category's own budget
    # window must not count as rollover, even when the global baseline is earlier.
    # alloc_by_cat entries are in year_month order (ORDER BY in the records query).
    cat_first_alloc_start = {cat_id: fiscal_window(allocs[0][0], day)[0] for cat_id, allocs in alloc_by_cat.items()}

    # ── SQL: cumulative spending using per-category start dates ───────────────
    # UNION ALL instead of VALUES (...) keeps the CTE construction simple.
    # Pass start dates as Python date objects so SQLAlchemy/psycopg2 bind them as DATE.
    params: dict = {"end_date": month_end}
    union_rows: list[str] = []
    for i, (cat_id, start) in enumerate(cat_first_alloc_start.items()):
        params[f"cid_{i}"] = cat_id
        params[f"sd_{i}"] = start
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
            Transaction.date >= month_start,
            Transaction.date <= month_end,
            Transaction.deleted_at.is_(None),
        )
        .group_by(Transaction.category_id)
        .all()
    )
    this_month_map = {r[0]: float(r[1] or 0) for r in this_month_rows}

    # ── Cumulative allocated: step-function carry-forward via window function ─
    cumulative_alloc_map: dict[int, float] = {}
    if all_months:
        # Compute the month after target for LEAD default value
        _y, _m = int(year_month[:4]), int(year_month[5:])
        _m += 1
        if _m > 12:
            _m, _y = 1, _y + 1
        year_month_next = f"{_y:04d}-{_m:02d}"
        cumulative_alloc_rows = db.execute(
            text("""
            WITH allocation_spans AS (
                SELECT
                    category_id,
                    year_month,
                    amount,
                    LEAD(year_month, 1, :year_month_next) OVER (
                        PARTITION BY category_id
                        ORDER BY year_month
                    ) AS next_year_month
                FROM budget_allocations
                WHERE year_month <= :year_month
            )
            SELECT
                category_id,
                SUM(
                    amount *
                    (
                        (DATE_PART('year', DATE_TRUNC('month', (next_year_month || '-01')::date))
                         - DATE_PART('year', GREATEST(
                               DATE_TRUNC('month', (:baseline || '-01')::date),
                               DATE_TRUNC('month', (year_month || '-01')::date)
                           ))
                        ) * 12
                        + (DATE_PART('month', DATE_TRUNC('month', (next_year_month || '-01')::date))
                           - DATE_PART('month', GREATEST(
                                 DATE_TRUNC('month', (:baseline || '-01')::date),
                                 DATE_TRUNC('month', (year_month || '-01')::date)
                             ))
                        )
                    )
                ) AS cumulative_allocated
            FROM allocation_spans
            GROUP BY category_id
            """),
            {"year_month": year_month, "year_month_next": year_month_next, "baseline": baseline},
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
