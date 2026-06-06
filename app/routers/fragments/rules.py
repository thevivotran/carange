"""HTMX fragments for the rules list."""

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import Category, TransactionRule, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


@router.get("/list")
def rules_list(request: Request, db: Session = Depends(get_db)):
    rules = db.query(TransactionRule).order_by(TransactionRule.priority.asc(), TransactionRule.id.asc()).all()
    categories = {c.id: c.name for c in db.query(Category).all()}
    rows = []
    for r in rules:
        try:
            raw = r.action_json
            if isinstance(raw, dict):
                action = raw
            else:
                action = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            action = {}
        cat_id = action.get("set_category_id")
        rows.append(
            {
                "id": r.id,
                "name": r.name,
                "is_active": r.is_active,
                "priority": r.priority,
                "match_field": r.match_field,
                "match_op": r.match_op,
                "match_value": r.match_value,
                "action_json": action,
                "action_json_str": json.dumps(action),
                "category_name": categories.get(cat_id, "—") if cat_id else "—",
                "match_count": r.match_count or 0,
                "last_matched_at": r.last_matched_at,
            }
        )
    return render_fragment(request, "rules/list.html", {"rules": rows})
