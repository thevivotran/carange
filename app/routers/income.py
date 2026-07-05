from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.database import CompensationEvent, IncomeSource, get_db
from app.models.schemas import (
    CompensationEvent as CompensationEventSchema,
    CompensationEventCreate,
    CompensationEventUpdate,
    IncomeSource as IncomeSourceSchema,
    IncomeSourceCreate,
    IncomeSourceUpdate,
)
from app.services import income_service

router = APIRouter()


# ---------------------------------------------------------------------------
# Income Source CRUD
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[IncomeSourceSchema])
def get_income_sources(user_id: Optional[int] = None, db: Session = Depends(get_db)):
    return income_service.get_income_sources(db, user_id=user_id)


@router.get("/{income_source_id}", response_model=IncomeSourceSchema)
def get_income_source(income_source_id: int, db: Session = Depends(get_db)):
    source = income_service.get_income_source(db, income_source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")
    return source


@router.post("/", response_model=IncomeSourceSchema)
def create_income_source(source: IncomeSourceCreate, db: Session = Depends(get_db)):
    return income_service.create_income_source(db, source)


@router.put("/{income_source_id}", response_model=IncomeSourceSchema)
def update_income_source(income_source_id: int, source_update: IncomeSourceUpdate, db: Session = Depends(get_db)):
    db_source = db.query(IncomeSource).filter(IncomeSource.id == income_source_id).first()
    if not db_source:
        raise HTTPException(status_code=404, detail="Income source not found")
    return income_service.update_income_source(db, db_source, source_update)


@router.delete("/{income_source_id}")
def delete_income_source(income_source_id: int, db: Session = Depends(get_db)):
    db_source = db.query(IncomeSource).filter(IncomeSource.id == income_source_id).first()
    if not db_source:
        raise HTTPException(status_code=404, detail="Income source not found")
    income_service.delete_income_source(db, db_source)
    return {"message": "Income source deleted successfully"}


# ---------------------------------------------------------------------------
# Compensation Event Routes
# ---------------------------------------------------------------------------


@router.get("/{income_source_id}/events", response_model=List[CompensationEventSchema])
def get_events(income_source_id: int, db: Session = Depends(get_db)):
    source = db.query(IncomeSource).filter(IncomeSource.id == income_source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")
    return income_service.get_events(db, income_source_id)


@router.post("/{income_source_id}/events", response_model=CompensationEventSchema)
def create_event(income_source_id: int, event: CompensationEventCreate, db: Session = Depends(get_db)):
    source = db.query(IncomeSource).filter(IncomeSource.id == income_source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")
    return income_service.create_event(db, source, event)


@router.patch("/{income_source_id}/events/{event_id}", response_model=CompensationEventSchema)
def update_event(
    income_source_id: int, event_id: int, event_update: CompensationEventUpdate, db: Session = Depends(get_db)
):
    source = db.query(IncomeSource).filter(IncomeSource.id == income_source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")

    event = (
        db.query(CompensationEvent)
        .filter(CompensationEvent.id == event_id, CompensationEvent.income_source_id == income_source_id)
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Compensation event not found")

    return income_service.update_event(db, event, event_update)


@router.delete("/{income_source_id}/events/{event_id}")
def delete_event(income_source_id: int, event_id: int, db: Session = Depends(get_db)):
    source = db.query(IncomeSource).filter(IncomeSource.id == income_source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Income source not found")

    event = (
        db.query(CompensationEvent)
        .filter(CompensationEvent.id == event_id, CompensationEvent.income_source_id == income_source_id)
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Compensation event not found")

    income_service.delete_event(db, event)
    return {"message": "Compensation event deleted successfully"}
