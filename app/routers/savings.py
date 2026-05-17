from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_
from typing import List, Optional
from datetime import datetime, timezone

from app.models.database import (
    get_db,
    SavingsBundle,
    SavingsStatus,
    FinancialProject,
    Transaction,
)
from app.models.schemas import SavingsBundle as SavingsBundleSchema, SavingsBundleCreate, SavingsBundleUpdate
from app.services import savings_service

router = APIRouter()


@router.get("/", response_model=List[SavingsBundleSchema])
def get_savings_bundles(status: Optional[str] = None, skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    from sqlalchemy import func

    query = (
        db.query(SavingsBundle, func.count(Transaction.id).label("tx_count"))
        .outerjoin(
            Transaction,
            and_(Transaction.savings_bundle_id == SavingsBundle.id, Transaction.deleted_at.is_(None)),
        )
        .filter(SavingsBundle.deleted_at.is_(None))
        .group_by(SavingsBundle.id)
    )

    if status:
        query = query.filter(SavingsBundle.status == status)

    results = query.order_by(SavingsBundle.created_at.desc()).offset(skip).limit(limit).all()
    bundles = []
    for bundle, count in results:
        bundle.linked_transaction_count = count
        bundles.append(bundle)
    return bundles


@router.get("/trash", response_model=List[SavingsBundleSchema])
def get_trashed_bundles(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return savings_service.get_trashed_bundles(db, skip=skip, limit=limit)


@router.get("/{bundle_id}", response_model=SavingsBundleSchema)
def get_savings_bundle(bundle_id: int, db: Session = Depends(get_db)):
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id, SavingsBundle.deleted_at.is_(None)).first()
    if not bundle:
        raise HTTPException(status_code=404, detail="Savings bundle not found")
    return bundle


@router.post("/", response_model=SavingsBundleSchema)
def create_savings_bundle(bundle: SavingsBundleCreate, db: Session = Depends(get_db)):
    # If linked to project, verify project exists
    if bundle.linked_project_id:
        project = db.query(FinancialProject).filter(FinancialProject.id == bundle.linked_project_id).first()
        if not project:
            raise HTTPException(status_code=404, detail="Linked project not found")

    bundle_data = bundle.model_dump()
    # Initialize current_amount with initial_deposit if not provided
    if "current_amount" not in bundle_data or bundle_data["current_amount"] is None:
        bundle_data["current_amount"] = bundle_data["initial_deposit"]
    db_bundle = SavingsBundle(**bundle_data)
    db.add(db_bundle)
    db.commit()
    db.refresh(db_bundle)
    return db_bundle


@router.put("/{bundle_id}", response_model=SavingsBundleSchema)
def update_savings_bundle(bundle_id: int, bundle_update: SavingsBundleUpdate, db: Session = Depends(get_db)):
    db_bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first()
    if not db_bundle:
        raise HTTPException(status_code=404, detail="Savings bundle not found")

    update_data = bundle_update.model_dump(exclude_unset=True)

    if "linked_project_id" in update_data and update_data["linked_project_id"] is not None:
        project = db.query(FinancialProject).filter(FinancialProject.id == update_data["linked_project_id"]).first()
        if not project:
            raise HTTPException(status_code=404, detail="Linked project not found")

    for key, value in update_data.items():
        setattr(db_bundle, key, value)

    # If status changed to completed, set completed_at
    if bundle_update.status == SavingsStatus.COMPLETED and not db_bundle.completed_at:
        db_bundle.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(db_bundle)
    return db_bundle


@router.delete("/{bundle_id}/hard")
def hard_delete_savings_bundle(bundle_id: int, db: Session = Depends(get_db)):
    """Permanently delete a soft-deleted savings bundle."""
    try:
        savings_service.hard_delete_bundle(db, bundle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Savings bundle not found in trash")
    return {"message": "Savings bundle permanently deleted"}


@router.post("/{bundle_id}/restore", response_model=SavingsBundleSchema)
def restore_savings_bundle(bundle_id: int, db: Session = Depends(get_db)):
    """Restore a soft-deleted savings bundle."""
    try:
        return savings_service.restore_bundle(db, bundle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Savings bundle not found in trash")


@router.delete("/{bundle_id}")
def delete_savings_bundle(bundle_id: int, db: Session = Depends(get_db)):
    """Soft-delete a savings bundle."""
    try:
        savings_service.soft_delete_bundle(db, bundle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Savings bundle not found")
    return {"message": "Savings bundle deleted successfully"}


@router.post("/{bundle_id}/mark-completed")
def mark_bundle_completed(bundle_id: int, db: Session = Depends(get_db)):
    try:
        bundle = savings_service.mark_bundle_completed(db, bundle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Savings bundle not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": "Bundle marked as completed", "completed_at": bundle.completed_at}


@router.post("/{bundle_id}/rollover", response_model=SavingsBundleSchema)
def rollover_bundle(bundle_id: int, db: Session = Depends(get_db)):
    try:
        return savings_service.rollover_bundle(db, bundle_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Savings bundle not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/stats/summary")
def get_savings_summary(db: Session = Depends(get_db)):
    active_bundles = db.query(SavingsBundle).filter(SavingsBundle.status == SavingsStatus.ACTIVE).all()

    total_initial = sum(b.initial_deposit for b in active_bundles)
    total_future = sum(b.future_amount for b in active_bundles)

    # Calculate total interest
    total_interest = total_future - total_initial

    return {
        "active_bundles_count": len(active_bundles),
        "total_initial_deposit": total_initial,
        "total_future_amount": total_future,
        "total_interest_earned": total_interest,
        "average_interest_rate": round((total_interest / total_initial * 100), 2) if total_initial > 0 else 0,
    }
