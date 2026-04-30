from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List

from app.models.database import get_db, Category, Transaction, TransactionType
from app.models.schemas import Category as CategorySchema, CategoryCreate, CategoryUpdate

router = APIRouter()

@router.get("/", response_model=List[CategorySchema])
def get_categories(
    type: str = None,
    is_active: bool = None,
    db: Session = Depends(get_db)
):
    query = db.query(
        Category,
        func.count(Transaction.id).label('tx_count')
    ).outerjoin(
        Transaction, Transaction.category_id == Category.id
    ).group_by(Category.id)

    if type:
        query = query.filter(Category.type == type)
    if is_active is not None:
        query = query.filter(Category.is_active == is_active)

    results = query.order_by(Category.type, Category.name).all()
    categories = []
    for cat, count in results:
        cat.transaction_count = count
        categories.append(cat)
    return categories

@router.get("/{category_id}", response_model=CategorySchema)
def get_category(category_id: int, db: Session = Depends(get_db)):
    result = db.query(
        Category,
        func.count(Transaction.id).label('tx_count'),
    ).outerjoin(
        Transaction, Transaction.category_id == Category.id
    ).filter(Category.id == category_id).group_by(Category.id).first()
    if not result:
        raise HTTPException(status_code=404, detail="Category not found")
    category, count = result
    category.transaction_count = count
    return category

@router.post("/", response_model=CategorySchema)
def create_category(category: CategoryCreate, db: Session = Depends(get_db)):
    # Check if category with same name and type already exists
    existing = db.query(Category).filter(
        Category.name == category.name,
        Category.type == category.type
    ).first()
    
    if existing:
        raise HTTPException(status_code=400, detail="Category with this name and type already exists")
    
    db_category = Category(**category.model_dump())
    db.add(db_category)
    db.commit()
    db.refresh(db_category)
    return db_category

@router.put("/{category_id}", response_model=CategorySchema)
def update_category(category_id: int, category: CategoryUpdate, db: Session = Depends(get_db)):
    db_category = db.query(Category).filter(Category.id == category_id).first()
    if not db_category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Check for duplicate name
    if category.name != db_category.name:
        existing = db.query(Category).filter(
            Category.name == category.name,
            Category.type == category.type,
            Category.id != category_id
        ).first()
        
        if existing:
            raise HTTPException(status_code=400, detail="Category with this name already exists")
    
    for key, value in category.model_dump(exclude_unset=True).items():
        setattr(db_category, key, value)
    
    db.commit()
    db.refresh(db_category)
    return db_category

@router.delete("/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Check if category has transactions
    transaction_count = db.query(Transaction).filter(Transaction.category_id == category_id).count()
    if transaction_count > 0:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete category. It has {transaction_count} transactions. Deactivate it instead."
        )
    
    db.delete(category)
    db.commit()
    return {"message": "Category deleted successfully"}

@router.patch("/{category_id}/toggle-active")
def toggle_category_active(category_id: int, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    category.is_active = not category.is_active
    db.commit()
    
    return {
        "message": f"Category {'activated' if category.is_active else 'deactivated'}",
        "is_active": category.is_active
    }