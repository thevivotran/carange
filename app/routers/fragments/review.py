"""HTMX fragments for the review inbox."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import Category, Transaction, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


@router.get("/list")
def review_list(request: Request, db: Session = Depends(get_db)):
    transactions = (
        db.query(Transaction)
        .filter(Transaction.needs_review == True, Transaction.deleted_at.is_(None))
        .order_by(Transaction.date.desc(), Transaction.confidence_score.asc())
        .limit(100)
        .all()
    )
    categories = db.query(Category).filter(Category.is_active == True).order_by(Category.name).all()
    count = len(transactions)
    return render_fragment(
        request,
        "review/list.html",
        {"transactions": transactions, "categories": categories, "count": count},
    )
