from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.database import Category, Transaction, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


@router.get("/rows")
def fragment_categories_rows(
    request: Request,
    type: str = "expense",
    sort_col: str = "name",
    sort_dir: str = "asc",
    db: Session = Depends(get_db),
):
    query = (
        db.query(Category, func.count(Transaction.id).label("tx_count"))
        .outerjoin(Transaction, and_(Transaction.category_id == Category.id, Transaction.deleted_at.is_(None)))
        .group_by(Category.id)
        .filter(Category.type == type)
    )

    categories = []
    for cat, count in query.all():
        cat.transaction_count = count
        categories.append(cat)

    reverse = sort_dir == "desc"
    if sort_col == "name":
        categories.sort(key=lambda c: c.name.lower(), reverse=reverse)
    elif sort_col == "count":
        categories.sort(key=lambda c: c.transaction_count or 0, reverse=reverse)
    elif sort_col == "status":
        categories.sort(key=lambda c: 0 if c.is_active else 1, reverse=reverse)

    return render_fragment(
        request,
        "partials/categories/_table_body.html",
        {"categories": categories, "sort_col": sort_col, "sort_dir": sort_dir},
    )
