from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.database import get_db, Transaction, TransactionType
from app.routers.fragments._helpers import render_fragment
from app.services.budget_service import compute_budget_rows
from app.services.fiscal_period import fiscal_window, current_period_label, get_month_start_day

router = APIRouter()


@router.get("/rows")
def fragment_budget_rows(
    request: Request,
    year_month: str = "",
    db: Session = Depends(get_db),
):
    from datetime import date as _date

    day = get_month_start_day(db)

    if not year_month or not year_month.strip():
        year_month = current_period_label(_date.today(), day)

    rows = compute_budget_rows(db, year_month, day)

    month_start, month_end = fiscal_window(year_month, day)
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
