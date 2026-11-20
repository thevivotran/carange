"""Review inbox — approve, reject, or edit auto-ingested transactions."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import Category, Transaction, TransactionType, get_db

router = APIRouter()


class ApprovePayload(BaseModel):
    category_id: Optional[int] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    date: Optional[str] = None
    payment_method: Optional[str] = None
    type: Optional[TransactionType] = None


@router.get("/count")
def review_count(db: Session = Depends(get_db)):
    """Return the current review inbox size."""
    count = db.query(Transaction).filter(Transaction.needs_review == True, Transaction.deleted_at.is_(None)).count()
    return {"count": count}


class BulkApprovePayload(BaseModel):
    min_confidence: float = 0.8


@router.post("/approve-bulk")
def approve_bulk(payload: BulkApprovePayload = BulkApprovePayload(), db: Session = Depends(get_db)):
    """Confirm every pending item read with at least `min_confidence`. Lower-confidence
    items stay in the inbox for a closer look."""
    txs = (
        db.query(Transaction)
        .filter(
            Transaction.needs_review == True,
            Transaction.deleted_at.is_(None),
            Transaction.confidence_score >= payload.min_confidence,
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    for tx in txs:
        tx.needs_review = False
        tx.updated_at = now
    db.commit()
    return {"approved": len(txs)}


@router.post("/{tx_id}/approve")
def approve(tx_id: int, payload: ApprovePayload = ApprovePayload(), db: Session = Depends(get_db)):
    """Clear needs_review and optionally update fields."""
    tx = _get_review_tx(tx_id, db)
    if payload.type is not None:
        tx.type = payload.type
    if payload.category_id is not None:
        tx.category_id = payload.category_id
    if tx.category_id is not None and (payload.type is not None or payload.category_id is not None):
        category = db.query(Category).filter(Category.id == tx.category_id).first()
        if category is None:
            raise HTTPException(status_code=400, detail="Unknown category")
        if category.type != tx.type:
            raise HTTPException(
                status_code=400,
                detail=f"'{category.name}' is a {category.type.value} category — choose one for {tx.type.value}",
            )
    if payload.description is not None:
        tx.description = payload.description
    if payload.amount is not None:
        tx.amount = payload.amount
    if payload.date is not None:
        from datetime import date

        try:
            tx.date = date.fromisoformat(payload.date)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date '{payload.date}': must be ISO format YYYY-MM-DD. ({exc})",
            ) from exc
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
