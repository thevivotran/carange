from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import Optional

from app.models.database import Category, TransactionTemplate, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


@router.get("/rows")
def fragment_templates_rows(
    request: Request,
    type: str = "",
    category_id: Optional[str] = None,
    is_active: str = "",
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    query = db.query(TransactionTemplate)
    if type:
        query = query.filter(TransactionTemplate.type == type)
    if category_id:
        try:
            query = query.filter(TransactionTemplate.category_id == int(category_id))
        except (ValueError, TypeError):
            pass
    if is_active == "true":
        query = query.filter(TransactionTemplate.is_active == True)  # noqa: E712
    elif is_active == "false":
        query = query.filter(TransactionTemplate.is_active == False)  # noqa: E712

    templates = query.order_by(TransactionTemplate.name).offset(skip).limit(limit).all()

    cat_ids = {t.category_id for t in templates}
    cats = {c.id: c for c in db.query(Category).filter(Category.id.in_(cat_ids)).all()}
    for t in templates:
        t.category = cats.get(t.category_id)

    return render_fragment(
        request,
        "partials/templates/_table_body.html",
        {"templates": templates},
        trigger_events={
            "updatePagination": {
                "count": len(templates),
                "skip": skip,
                "limit": limit,
            }
        },
    )
