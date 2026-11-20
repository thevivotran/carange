from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database import get_db, Note
from app.models.schemas import Note as NoteSchema, NoteCreate, NoteUpdate

router = APIRouter()


@router.get("/", response_model=List[NoteSchema])
def get_notes(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return (
        db.query(Note).order_by(func.coalesce(Note.updated_at, Note.created_at).desc()).offset(skip).limit(limit).all()
    )


@router.get("/summary")
def iou_summary(db: Session = Depends(get_db)):
    """Totals of unsettled "money owed" notes, by direction."""
    rows = (
        db.query(Note.direction, func.coalesce(func.sum(Note.amount), 0))
        .filter(Note.type == "money_owed", Note.settled_at.is_(None), Note.amount.isnot(None))
        .group_by(Note.direction)
        .all()
    )
    totals = {direction: float(total) for direction, total in rows}
    return {"owed_to_us": totals.get("owed_to_us", 0.0), "we_owe": totals.get("we_owe", 0.0)}


@router.post("/", response_model=NoteSchema)
def create_note(note: NoteCreate, db: Session = Depends(get_db)):
    db_note = Note(**note.model_dump())
    db.add(db_note)
    db.commit()
    db.refresh(db_note)
    return db_note


@router.post("/{note_id}/settle", response_model=NoteSchema)
def settle_note(note_id: int, db: Session = Depends(get_db)):
    """Mark an IOU settled. Deliberately creates no transaction: the user logs the
    payment through the normal add-transaction form, so ledger classification is
    unchanged (see app/services/ledger.py)."""
    db_note = db.query(Note).filter(Note.id == note_id).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    if db_note.type != "money_owed":
        raise HTTPException(status_code=400, detail="Only money-owed notes can be settled")
    db_note.settled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(db_note)
    return db_note


@router.put("/{note_id}", response_model=NoteSchema)
def update_note(note_id: int, note: NoteUpdate, db: Session = Depends(get_db)):
    db_note = db.query(Note).filter(Note.id == note_id).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    for key, value in note.model_dump(exclude_unset=True).items():
        setattr(db_note, key, value)
    db.commit()
    db.refresh(db_note)
    return db_note


@router.delete("/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    db_note = db.query(Note).filter(Note.id == note_id).first()
    if not db_note:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete(db_note)
    db.commit()
    return {"message": "Note deleted"}
