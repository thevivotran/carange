"""HTMX fragments for the payees list."""

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import Payee, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


@router.get("/list")
def payees_list(request: Request, db: Session = Depends(get_db)):
    payees = db.query(Payee).order_by(Payee.canonical_name).all()
    rows = []
    for p in payees:
        try:
            patterns = json.loads(p.alias_patterns or "[]")
        except (json.JSONDecodeError, TypeError):
            patterns = []
        rows.append(
            {
                "id": p.id,
                "canonical_name": p.canonical_name,
                "default_category_id": p.default_category_id,
                "alias_patterns": patterns,
                "alias_patterns_str": json.dumps(patterns),
                "source": p.source,
                "created_at": p.created_at,
            }
        )
    return render_fragment(request, "payees/list.html", {"payees": rows})
