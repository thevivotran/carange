from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.models.database import get_db, TransactionTemplate, Category
from app.models.schemas import TransactionTemplate as TransactionTemplateSchema, TransactionTemplateCreate, TransactionTemplateUpdate

router = APIRouter()

@router.get("/", response_model=List[TransactionTemplateSchema])
def get_templates(
    skip: int = 0,
    limit: int = 100,
    type: Optional[str] = None,
    category_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db)
):
    query = db.query(TransactionTemplate)
    
    if type:
        query = query.filter(TransactionTemplate.type == type)
    if category_id:
        query = query.filter(TransactionTemplate.category_id == category_id)
    if is_active is not None:
        query = query.filter(TransactionTemplate.is_active == is_active)
    
    return query.order_by(TransactionTemplate.name).offset(skip).limit(limit).all()

@router.get("/{template_id}", response_model=TransactionTemplateSchema)
def get_template(template_id: int, db: Session = Depends(get_db)):
    template = db.query(TransactionTemplate).filter(TransactionTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template

@router.post("/", response_model=TransactionTemplateSchema)
def create_template(template: TransactionTemplateCreate, db: Session = Depends(get_db)):
    # Verify category exists
    category = db.query(Category).filter(Category.id == template.category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    db_template = TransactionTemplate(**template.model_dump())
    db.add(db_template)
    db.commit()
    db.refresh(db_template)
    return db_template

@router.put("/{template_id}", response_model=TransactionTemplateSchema)
def update_template(template_id: int, template: TransactionTemplateUpdate, db: Session = Depends(get_db)):
    db_template = db.query(TransactionTemplate).filter(TransactionTemplate.id == template_id).first()
    if not db_template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    update_data = template.model_dump(exclude_unset=True)

    if "category_id" in update_data:
        category = db.query(Category).filter(Category.id == update_data["category_id"]).first()
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")

    for key, value in update_data.items():
        setattr(db_template, key, value)

    db.commit()
    db.refresh(db_template)
    return db_template

@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db)):
    template = db.query(TransactionTemplate).filter(TransactionTemplate.id == template_id).first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    db.delete(template)
    db.commit()
    return {"message": "Template deleted successfully"}
