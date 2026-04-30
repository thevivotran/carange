from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime, timezone
import calendar

from app.models.database import get_db, BudgetAllocation, Category, Transaction, TransactionType
from app.models.schemas import (
    BudgetAllocationCreate, BudgetAllocationUpdate,
    BudgetAllocationRecord, BudgetCategoryRow,
)

router = APIRouter()

BASELINE = "2026-05"   # first month budget tracking begins


def _months_range(from_ym: str, to_ym: str) -> list[str]:
    """Return list of YYYY-MM strings from from_ym up to and including to_ym."""
    months = []
    y, m = int(from_ym[:4]), int(from_ym[5:])
    ey, em = int(to_ym[:4]), int(to_ym[5:])
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def _end_of_month(year_month: str) -> str:
    y, m = int(year_month[:4]), int(year_month[5:])
    last = calendar.monthrange(y, m)[1]
    return f"{year_month}-{last:02d}"


def _compute_rows(db: Session, year_month: str) -> list[dict]:
    if year_month < BASELINE:
        return []

    months = _months_range(BASELINE, year_month)

    # All allocation records up to this month, grouped by category
    records = (
        db.query(BudgetAllocation)
        .filter(BudgetAllocation.year_month <= year_month)
        .order_by(BudgetAllocation.category_id, BudgetAllocation.year_month)
        .all()
    )

    # Build: category_id → sorted list of (year_month, amount, id)
    from collections import defaultdict
    alloc_by_cat: dict[int, list] = defaultdict(list)
    for r in records:
        alloc_by_cat[r.category_id].append((r.year_month, r.amount, r.id))

    # Determine which categories are active (have at least one allocation record)
    active_cat_ids = list(alloc_by_cat.keys())
    if not active_cat_ids:
        return []

    # Fetch category metadata
    cats = {c.id: c for c in db.query(Category).filter(Category.id.in_(active_cat_ids)).all()}

    # Cumulative spending per category from BASELINE to end of year_month
    start_date = f"{BASELINE}-01"
    end_date   = _end_of_month(year_month)

    spent_rows = (
        db.query(Transaction.category_id, func.sum(Transaction.amount).label("total"))
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.category_id.in_(active_cat_ids),
            Transaction.date >= start_date,
            Transaction.date <= end_date,
        )
        .group_by(Transaction.category_id)
        .all()
    )
    cumulative_spent_map = {r.category_id: r.total or 0 for r in spent_rows}

    # This-month spending
    month_start = f"{year_month}-01"
    month_end   = _end_of_month(year_month)
    this_month_rows = (
        db.query(Transaction.category_id, func.sum(Transaction.amount).label("total"))
        .filter(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.category_id.in_(active_cat_ids),
            Transaction.date >= month_start,
            Transaction.date <= month_end,
        )
        .group_by(Transaction.category_id)
        .all()
    )
    this_month_map = {r.category_id: r.total or 0 for r in this_month_rows}

    result = []
    for cat_id, allocs in alloc_by_cat.items():
        cat = cats.get(cat_id)
        if not cat:
            continue

        # For each month in range, resolve the applicable allocation amount
        # (use the most recent allocation <= that month)
        cumulative_allocated = 0.0
        current_alloc_amount = 0.0
        current_alloc_id = None
        alloc_idx = 0

        for month in months:
            # Advance pointer through allocation history
            while alloc_idx < len(allocs) and allocs[alloc_idx][0] <= month:
                current_alloc_amount = allocs[alloc_idx][1]
                current_alloc_id     = allocs[alloc_idx][2]
                alloc_idx += 1
            cumulative_allocated += current_alloc_amount

        cumulative_spent  = cumulative_spent_map.get(cat_id, 0)
        this_month_spent  = this_month_map.get(cat_id, 0)
        available_balance = cumulative_allocated - cumulative_spent
        usage_pct = (this_month_spent / current_alloc_amount * 100) if current_alloc_amount else 0

        result.append({
            "category_id":          cat_id,
            "category_name":        cat.name,
            "category_color":       cat.color,
            "monthly_allocation":   current_alloc_amount,
            "cumulative_allocated": cumulative_allocated,
            "cumulative_spent":     cumulative_spent,
            "this_month_spent":     this_month_spent,
            "available_balance":    available_balance,
            "usage_pct":            round(usage_pct, 1),
            "allocation_id":        current_alloc_id,
        })

    result.sort(key=lambda r: r["category_name"])
    return result


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/{year_month}/rows")
def get_budget_rows(year_month: str, db: Session = Depends(get_db)):
    return _compute_rows(db, year_month)


@router.get("/allocations/{year_month}", response_model=List[BudgetAllocationRecord])
def get_allocations_for_month(year_month: str, db: Session = Depends(get_db)):
    return (
        db.query(BudgetAllocation)
        .filter(BudgetAllocation.year_month == year_month)
        .all()
    )


@router.get("/categories/unbudgeted/{year_month}")
def get_unbudgeted_categories(year_month: str, db: Session = Depends(get_db)):
    """Return expense categories that have no allocation on or before year_month."""
    budgeted_ids = [
        r.category_id
        for r in db.query(BudgetAllocation.category_id)
        .filter(BudgetAllocation.year_month <= year_month)
        .distinct()
        .all()
    ]
    query = db.query(Category).filter(Category.type == TransactionType.EXPENSE)
    if budgeted_ids:
        query = query.filter(~Category.id.in_(budgeted_ids))
    return [{"id": c.id, "name": c.name, "color": c.color} for c in query.order_by(Category.name).all()]


@router.post("/", response_model=BudgetAllocationRecord)
def set_allocation(data: BudgetAllocationCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(BudgetAllocation)
        .filter(
            BudgetAllocation.category_id == data.category_id,
            BudgetAllocation.year_month  == data.year_month,
        )
        .first()
    )
    if existing:
        existing.amount     = data.amount
        existing.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(existing)
        return existing

    alloc = BudgetAllocation(**data.model_dump())
    db.add(alloc)
    db.commit()
    db.refresh(alloc)
    return alloc


@router.put("/{allocation_id}", response_model=BudgetAllocationRecord)
def update_allocation(allocation_id: int, data: BudgetAllocationUpdate, db: Session = Depends(get_db)):
    alloc = db.query(BudgetAllocation).filter(BudgetAllocation.id == allocation_id).first()
    if not alloc:
        raise HTTPException(status_code=404, detail="Allocation not found")
    alloc.amount     = data.amount
    alloc.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(alloc)
    return alloc


@router.delete("/category/{category_id}")
def delete_category_budget(category_id: int, db: Session = Depends(get_db)):
    deleted = (
        db.query(BudgetAllocation)
        .filter(BudgetAllocation.category_id == category_id)
        .delete()
    )
    db.commit()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="No allocations found for this category")
    return {"message": f"Deleted {deleted} allocation(s)"}


@router.delete("/{allocation_id}")
def delete_allocation(allocation_id: int, db: Session = Depends(get_db)):
    alloc = db.query(BudgetAllocation).filter(BudgetAllocation.id == allocation_id).first()
    if not alloc:
        raise HTTPException(status_code=404, detail="Allocation not found")
    db.delete(alloc)
    db.commit()
    return {"message": "Allocation deleted"}
