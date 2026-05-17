from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime, timezone

from app.models.database import get_db, BudgetAllocation, Category, Transaction, TransactionType
from app.models.schemas import (
    BudgetAllocationCreate,
    BudgetAllocationUpdate,
    BudgetAllocationRecord,
)
from app.services.budget_service import compute_budget_rows, end_of_month

_compute_rows = compute_budget_rows

router = APIRouter()


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/{year_month}/rows")
def get_budget_rows(year_month: str, db: Session = Depends(get_db)):
    return _compute_rows(db, year_month)


@router.get("/allocations/{year_month}", response_model=List[BudgetAllocationRecord])
def get_allocations_for_month(year_month: str, db: Session = Depends(get_db)):
    return db.query(BudgetAllocation).filter(BudgetAllocation.year_month == year_month).all()


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
            BudgetAllocation.year_month == data.year_month,
        )
        .first()
    )
    if existing:
        existing.amount = data.amount
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
    alloc.amount = data.amount
    alloc.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(alloc)
    return alloc


@router.get("/{year_month}/monthly-income")
def get_monthly_income(year_month: str, db: Session = Depends(get_db)):
    """Return total income recorded for year_month."""
    month_start = f"{year_month}-01"
    month_end = end_of_month(year_month)
    total = (
        db.query(func.sum(Transaction.amount))
        .filter(
            Transaction.type == TransactionType.INCOME,
            Transaction.date >= month_start,
            Transaction.date <= month_end,
            Transaction.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    return {"income": float(total)}


@router.delete("/category/{category_id}")
def delete_category_budget(category_id: int, db: Session = Depends(get_db)):
    deleted = db.query(BudgetAllocation).filter(BudgetAllocation.category_id == category_id).delete()
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
