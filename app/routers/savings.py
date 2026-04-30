from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, datetime, timezone

from app.models.database import get_db, SavingsBundle, SavingsStatus, SavingsType, FinancialProject, Transaction, TransactionType, Category
from app.models.schemas import SavingsBundle as SavingsBundleSchema, SavingsBundleCreate, SavingsBundleUpdate

router = APIRouter()

@router.get("/", response_model=List[SavingsBundleSchema])
def get_savings_bundles(
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(SavingsBundle)
    
    if status:
        query = query.filter(SavingsBundle.status == status)
    
    bundles = query.order_by(SavingsBundle.created_at.desc()).offset(skip).limit(limit).all()
    return bundles

@router.get("/{bundle_id}", response_model=SavingsBundleSchema)
def get_savings_bundle(bundle_id: int, db: Session = Depends(get_db)):
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first()
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
    if 'current_amount' not in bundle_data or bundle_data['current_amount'] is None:
        bundle_data['current_amount'] = bundle_data['initial_deposit']
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
    
    # Update fields
    update_data = bundle_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_bundle, key, value)
    
    # If status changed to completed, set completed_at
    if bundle_update.status == SavingsStatus.COMPLETED and not db_bundle.completed_at:
        db_bundle.completed_at = datetime.now(timezone.utc)
    
    db.commit()
    db.refresh(db_bundle)
    return db_bundle

@router.delete("/{bundle_id}")
def delete_savings_bundle(bundle_id: int, db: Session = Depends(get_db)):
    from app.models.database import Transaction
    
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first()
    if not bundle:
        raise HTTPException(status_code=404, detail="Savings bundle not found")
    
    # Unlink any transactions associated with this bundle
    db.query(Transaction).filter(Transaction.savings_bundle_id == bundle_id).update({"savings_bundle_id": None})
    
    # Now delete the bundle
    db.delete(bundle)
    db.commit()
    return {"message": "Savings bundle deleted successfully"}

@router.post("/{bundle_id}/mark-completed")
def mark_bundle_completed(bundle_id: int, db: Session = Depends(get_db)):
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first()
    if not bundle:
        raise HTTPException(status_code=404, detail="Savings bundle not found")
    
    if bundle.status != SavingsStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Only active bundles can be marked as completed")

    bundle.status = SavingsStatus.COMPLETED
    bundle.completed_at = datetime.now(timezone.utc)

    category = (
        db.query(Category)
        .filter(Category.type == TransactionType.INCOME, Category.name == "Investment")
        .first()
        or db.query(Category)
        .filter(Category.type == TransactionType.INCOME)
        .first()
    )
    if category:
        transaction = Transaction(
            date=date.today(),
            amount=bundle.future_amount,
            type=TransactionType.INCOME,
            category_id=category.id,
            description=f"Savings matured: {bundle.name} - {bundle.bank_name}",
            payment_method="bank",
            is_savings_related=True,
            savings_bundle_id=bundle.id,
        )
        db.add(transaction)

    db.commit()

    return {"message": "Bundle marked as completed", "completed_at": bundle.completed_at}

@router.post("/{bundle_id}/rollover", response_model=SavingsBundleSchema)
def rollover_bundle(bundle_id: int, db: Session = Depends(get_db)):
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id).first()
    if not bundle:
        raise HTTPException(status_code=404, detail="Savings bundle not found")

    if bundle.status != SavingsStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Only active bundles can be rolled over")

    # Mark old bundle as completed
    bundle.status = SavingsStatus.COMPLETED
    bundle.completed_at = datetime.now(timezone.utc)

    # Create new bundle seeded with the matured amount.
    # future_amount is left equal to initial_deposit as a placeholder — the user
    # should edit it once the new term's projected amount is known.
    today = date.today()
    original_term_days = (bundle.maturity_date - bundle.start_date).days if bundle.maturity_date else None
    new_maturity = date.fromordinal(today.toordinal() + original_term_days) if original_term_days else None
    new_bundle = SavingsBundle(
        name=f"{bundle.name} (Rollover)",
        bank_name=bundle.bank_name,
        type=bundle.type,
        initial_deposit=bundle.future_amount,
        current_amount=bundle.future_amount,
        future_amount=bundle.future_amount,
        interest_rate=bundle.interest_rate,
        start_date=today,
        maturity_date=new_maturity,
        status=SavingsStatus.ACTIVE,
        notes=f"Rolled over from bundle #{bundle.id}"
    )
    db.add(new_bundle)
    db.commit()
    db.refresh(new_bundle)
    return new_bundle


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
        "average_interest_rate": round((total_interest / total_initial * 100), 2) if total_initial > 0 else 0
    }