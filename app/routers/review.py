"""Review inbox — approve, reject, or edit auto-ingested transactions."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import Transaction, get_db
from app.services.rules_service import normalize_description

router = APIRouter()


class ApprovePayload(BaseModel):
    category_id: Optional[int] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    date: Optional[str] = None
    payment_method: Optional[str] = None


@router.get("/count")
def review_count(db: Session = Depends(get_db)):
    """Return the current review inbox size."""
    count = db.query(Transaction).filter(Transaction.needs_review == True, Transaction.deleted_at.is_(None)).count()
    return {"count": count}


@router.post("/{tx_id}/approve")
def approve(tx_id: int, payload: ApprovePayload = ApprovePayload(), db: Session = Depends(get_db)):
    """Clear needs_review and optionally update fields."""
    tx = _get_review_tx(tx_id, db)
    if payload.category_id is not None:
        tx.category_id = payload.category_id
    if payload.description is not None:
        _, payee_id = normalize_description(db, payload.description)
        tx.description = payload.description
        tx.payee_id = payee_id
    if payload.amount is not None:
        tx.amount = payload.amount
    if payload.date is not None:
        from datetime import date

        tx.date = date.fromisoformat(payload.date)
    if payload.payment_method is not None:
        tx.payment_method = payload.payment_method
    tx.needs_review = False
    tx.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(tx)
    return {"id": tx.id, "needs_review": tx.needs_review}


@router.post("/{tx_id}/reject")
def reject(tx_id: int, db: Session = Depends(get_db)):
    """Soft-delete a review transaction."""
    tx = _get_review_tx(tx_id, db)
    tx.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"id": tx.id, "deleted": True}


@router.get("/{tx_id}/rule-prefill")
def rule_prefill(tx_id: int, db: Session = Depends(get_db)):
    """Return pre-filled rule data for the 'Remember as rule' action."""
    tx = db.query(Transaction).filter(Transaction.id == tx_id, Transaction.deleted_at.is_(None)).first()
    if not tx:
        raise HTTPException(404, "Transaction not found")
    return {
        "name": f"Auto: {tx.description or 'unnamed'}",
        "match_field": "description",
        "match_op": "contains",
        "match_value": tx.description or "",
        "action_json": {"set_category_id": tx.category_id, "auto_approve": True},
    }


def _get_review_tx(tx_id: int, db: Session) -> Transaction:
    tx = (
        db.query(Transaction)
        .filter(Transaction.id == tx_id, Transaction.needs_review == True, Transaction.deleted_at.is_(None))
        .first()
    )
    if not tx:
        raise HTTPException(404, "Review transaction not found")
    return tx
