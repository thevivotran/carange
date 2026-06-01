"""CRUD for payees (merchant/vendor dictionary)."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import Payee, get_db
from app.services.rules_service import invalidate_payee_cache

router = APIRouter()


class PayeeCreate(BaseModel):
    canonical_name: str
    default_category_id: Optional[int] = None
    alias_patterns: list[str] = []
    source: str = "manual"


class PayeeUpdate(BaseModel):
    canonical_name: Optional[str] = None
    default_category_id: Optional[int] = None
    alias_patterns: Optional[list[str]] = None
    source: Optional[str] = None


@router.get("/")
def list_payees(db: Session = Depends(get_db)):
    payees = db.query(Payee).order_by(Payee.canonical_name).all()
    return [_to_dict(p) for p in payees]


@router.post("/", status_code=201)
def create_payee(payload: PayeeCreate, db: Session = Depends(get_db)):
    existing = db.query(Payee).filter(Payee.canonical_name == payload.canonical_name).first()
    if existing:
        raise HTTPException(409, "Payee with this canonical name already exists")
    payee = Payee(
        canonical_name=payload.canonical_name,
        default_category_id=payload.default_category_id,
        alias_patterns=json.dumps(payload.alias_patterns),
        source=payload.source,
    )
    db.add(payee)
    db.commit()
    db.refresh(payee)
    invalidate_payee_cache()
    return _to_dict(payee)


@router.put("/{payee_id}")
def update_payee(payee_id: int, payload: PayeeUpdate, db: Session = Depends(get_db)):
    payee = _get(payee_id, db)
    if payload.canonical_name is not None:
        payee.canonical_name = payload.canonical_name
    if "default_category_id" in payload.model_fields_set:
        payee.default_category_id = payload.default_category_id
    if payload.alias_patterns is not None:
        payee.alias_patterns = json.dumps(payload.alias_patterns)
    if payload.source is not None:
        payee.source = payload.source
    db.commit()
    db.refresh(payee)
    invalidate_payee_cache()
    return _to_dict(payee)


@router.delete("/{payee_id}", status_code=204)
def delete_payee(payee_id: int, db: Session = Depends(get_db)):
    payee = _get(payee_id, db)
    db.delete(payee)
    db.commit()
    invalidate_payee_cache()


def _get(payee_id: int, db: Session) -> Payee:
    payee = db.query(Payee).filter(Payee.id == payee_id).first()
    if not payee:
        raise HTTPException(404, "Payee not found")
    return payee


def _to_dict(p: Payee) -> dict:
    patterns: list[str] = []
    try:
        patterns = json.loads(p.alias_patterns or "[]")
    except (json.JSONDecodeError, TypeError):
        pass
    return {
        "id": p.id,
        "canonical_name": p.canonical_name,
        "default_category_id": p.default_category_id,
        "alias_patterns": patterns,
        "source": p.source,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
