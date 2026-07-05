from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import User, get_db
from app.routers.fragments._helpers import render_fragment
from app.services import income_service

router = APIRouter()


@router.get("/grid")
def fragment_income_grid(
    request: Request,
    income_type: str = "",
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    sources = income_service.get_income_sources(db)
    if income_type:
        sources = [s for s in sources if s.income_type.value == income_type]
    if active_only:
        sources = [s for s in sources if s.is_active]

    users = db.query(User).order_by(User.created_at, User.id).all()
    groups = [{"user": user, "sources": [s for s in sources if s.user_id == user.id]} for user in users]

    return render_fragment(
        request,
        "partials/income/_income_grid.html",
        {
            "groups": groups,
            "total_count": len(sources),
            "active_count": sum(1 for s in sources if s.is_active),
            "review_overdue_count": sum(1 for s in sources if s.review_overdue),
        },
    )
