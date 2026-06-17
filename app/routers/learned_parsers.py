from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models.database import LearnedParser, get_db

# This app has no auth layer (single-user, homelab deployment, access-controlled
# at the ingress level). If multi-user auth is added later, these mutation
# endpoints (approve, delete) must be restricted to admin/operator role.
router = APIRouter(prefix="/learned-parsers", tags=["learned-parsers"])


@router.get("")
def list_parsers(db: Session = Depends(get_db)):
    rows = db.query(LearnedParser).order_by(LearnedParser.created_at.desc()).all()
    return [
        {
            "id": lp.id,
            "source_name": lp.source_name,
            "detection_keywords": lp.detection_keywords,
            "is_approved": lp.is_approved,
            "hit_count": lp.hit_count,
            "created_at": lp.created_at.isoformat() if lp.created_at else None,
            "last_used_at": lp.last_used_at.isoformat() if lp.last_used_at else None,
        }
        for lp in rows
    ]


@router.patch("/{parser_id}/approve")
def approve_parser(parser_id: int, db: Session = Depends(get_db)):
    lp = db.query(LearnedParser).filter(LearnedParser.id == parser_id).first()
    if lp is None:
        raise HTTPException(status_code=404, detail="Parser not found")
    lp.is_approved = True
    db.commit()
    return {"id": lp.id, "source_name": lp.source_name, "is_approved": True}


@router.delete("/{parser_id}")
def delete_parser(parser_id: int, db: Session = Depends(get_db)):
    lp = db.query(LearnedParser).filter(LearnedParser.id == parser_id).first()
    if lp is None:
        raise HTTPException(status_code=404, detail="Parser not found")
    db.delete(lp)
    db.commit()
    return {"deleted": parser_id}
