"""Shared budget-context helper — single source of truth for per-transaction budget snapshots."""

from datetime import date

from sqlalchemy.orm import Session


def status_word(usage_pct: float, left: float) -> str:
    if left < 0 or usage_pct >= 100:
        return "Over"
    if usage_pct >= 95:
        return "At risk"
    if usage_pct >= 75:
        return "Watch"
    return "On track"


def pace_label(usage_pct: float, days_elapsed_pct: float) -> str:
    gap = usage_pct - days_elapsed_pct
    if gap <= 5:
        return "On pace"
    if gap <= 20:
        return "Ahead of pace"
    return "Well ahead"


def pace(year_month: str, day: int, today: date) -> tuple[float, str]:
    from app.services.fiscal_period import days_in_period, fiscal_window

    start, end = fiscal_window(year_month, day)
    total = days_in_period(year_month, day)

    if today > end:
        days_elapsed_pct = 100.0
    elif today < start:
        days_elapsed_pct = 0.0
    else:
        days_elapsed_pct = round(((today - start).days + 1) / total * 100, 1)

    return days_elapsed_pct, ""


def budget_snapshot(
    db: Session,
    category_id: int,
    year_month: str,
    *,
    extra_amount: float = 0.0,
    day: int | None = None,
) -> dict | None:
    from app.services.budget_service import compute_budget_rows
    from app.services.fiscal_period import get_month_start_day

    if day is None:
        day = get_month_start_day(db)

    rows = compute_budget_rows(db, year_month, day)
    row = next((r for r in rows if r["category_id"] == category_id), None)
    if row is None or row["monthly_allocation"] <= 0:
        return None

    allocated = row["monthly_allocation"]
    spent = row["this_month_spent"]
    left = allocated - spent
    usage_pct = row["usage_pct"]
    days_elapsed_pct, _ = pace(year_month, day, date.today())

    snap = {
        "category_id": category_id,
        "category_name": row["category_name"],
        "category_color": row["category_color"],
        "allocated": allocated,
        "spent": spent,
        "left": left,
        "usage_pct": usage_pct,
        "status": status_word(usage_pct, left),
        "available_balance": row["available_balance"],
        "days_elapsed_pct": days_elapsed_pct,
        "pace_status": pace_label(usage_pct, days_elapsed_pct),
    }

    # Always emit projected_* so consumers (e.g. the live preview) never hit a
    # missing key; with no extra amount they mirror the current state.
    projected_spent = spent + max(extra_amount, 0.0)
    projected_left = allocated - projected_spent
    projected_usage_pct = round(projected_spent / allocated * 100, 1)
    snap["projected_spent"] = projected_spent
    snap["projected_left"] = projected_left
    snap["projected_usage_pct"] = projected_usage_pct
    snap["projected_status"] = status_word(projected_usage_pct, projected_left)

    return snap


def render_bar(pct: float, width: int = 10) -> str:
    clamped = max(0.0, min(100.0, pct))
    filled = round(clamped / 100 * width)
    return "█" * filled + "░" * (width - filled)
