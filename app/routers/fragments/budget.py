from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.database import get_db, Transaction, TransactionType
from app.routers.fragments._helpers import render_fragment
from app.services.budget_service import compute_budget_rows, end_of_month

router = APIRouter()


@router.get("/rows")
def fragment_budget_rows(
    request: Request,
    year_month: str = "",
    db: Session = Depends(get_db),
):
    from datetime import date as _date

    if not year_month or not year_month.strip():
        today = _date.today()
        year_month = f"{today.year}-{today.month:02d}"

    rows = compute_budget_rows(db, year_month)

    month_start = f"{year_month}-01"
    month_end = end_of_month(year_month)
    income = float(
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

    return render_fragment(
        request,
        "partials/budget/_category_rows.html",
        {"rows": rows, "income": income, "year_month": year_month},
    )
