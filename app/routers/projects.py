from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from datetime import date

from app.models.database import (
    get_db,
    FinancialProject,
    ProjectStatus,
    ProjectType,
    SavingsBundle,
    ProjectPayment,
)
from app.models.schemas import (
    FinancialProject as FinancialProjectSchema,
    FinancialProjectCreate,
    FinancialProjectUpdate,
    ProjectPayment as ProjectPaymentSchema,
    ProjectPaymentCreate,
    ProjectPaymentUpdate,
)
from app.services import project_service

router = APIRouter()


def _calc_progress(project) -> float:
    return project_service.calc_progress(project)


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@router.get("/trash", response_model=List[FinancialProjectSchema])
def get_trashed_projects(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    projects = project_service.get_trashed_projects(db, skip=skip, limit=limit)
    for p in projects:
        p.progress_percentage = _calc_progress(p)
    return projects


@router.get("/stats/summary")
def get_projects_summary(db: Session = Depends(get_db)):
    active_projects = (
        db.query(FinancialProject)
        .filter(
            FinancialProject.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS]),
            FinancialProject.deleted_at.is_(None),
        )
        .all()
    )

    total_target = sum(p.target_amount for p in active_projects)
    total_current = sum(p.current_amount for p in active_projects)

    completed_count = (
        db.query(FinancialProject)
        .filter(
            FinancialProject.status == ProjectStatus.COMPLETED,
            FinancialProject.deleted_at.is_(None),
        )
        .count()
    )

    return {
        "active_projects_count": len(active_projects),
        "completed_projects_count": completed_count,
        "total_target_amount": total_target,
        "total_current_amount": total_current,
        "progress_percentage": round((total_current / total_target * 100), 2) if total_target > 0 else 0,
    }


@router.get("/", response_model=List[FinancialProjectSchema])
def get_projects(
    status: Optional[ProjectStatus] = None,
    project_type: Optional[ProjectType] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    query = db.query(FinancialProject).filter(FinancialProject.deleted_at.is_(None))
    if status:
        query = query.filter(FinancialProject.status == status)
    if project_type:
        query = query.filter(FinancialProject.type == project_type)

    projects = query.order_by(FinancialProject.created_at.desc()).offset(skip).limit(limit).all()
    for p in projects:
        p.progress_percentage = _calc_progress(p)
    return projects


@router.get("/{project_id}", response_model=FinancialProjectSchema)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(
        FinancialProject.id == project_id, FinancialProject.deleted_at.is_(None)
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.progress_percentage = _calc_progress(project)
    return project


@router.post("/", response_model=FinancialProjectSchema)
def create_project(project: FinancialProjectCreate, db: Session = Depends(get_db)):
    db_project = FinancialProject(**project.model_dump(), target_amount=0, current_amount=0)
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project


@router.put("/{project_id}", response_model=FinancialProjectSchema)
def update_project(project_id: int, project_update: FinancialProjectUpdate, db: Session = Depends(get_db)):
    db_project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")

    update_data = project_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_project, key, value)

    if project_update.status == ProjectStatus.COMPLETED and not db_project.completed_at:
        db_project.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(db_project)
    return db_project


@router.delete("/{project_id}/hard")
def hard_delete_project(project_id: int, db: Session = Depends(get_db)):
    """Permanently delete a soft-deleted project."""
    try:
        project_service.hard_delete_project(db, project_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Project not found in trash")
    return {"message": "Project permanently deleted"}


@router.post("/{project_id}/restore", response_model=FinancialProjectSchema)
def restore_project(project_id: int, db: Session = Depends(get_db)):
    """Restore a soft-deleted project."""
    try:
        project = project_service.restore_project(db, project_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Project not found in trash")
    project.progress_percentage = _calc_progress(project)
    return project


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """Soft-delete a project."""
    try:
        project_service.soft_delete_project(db, project_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"message": "Project deleted successfully"}


# ---------------------------------------------------------------------------
# Payment Routes
# ---------------------------------------------------------------------------


@router.get("/{project_id}/payments", response_model=List[ProjectPaymentSchema])
def get_payments(project_id: int, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    payments = (
        db.query(ProjectPayment)
        .filter(ProjectPayment.project_id == project_id)
        .order_by(
            ProjectPayment.due_date.is_(None),
            ProjectPayment.due_date.asc(),
            ProjectPayment.sort_order.asc(),
            ProjectPayment.id.asc(),
        )
        .all()
    )
    return payments


@router.post("/{project_id}/payments", response_model=ProjectPaymentSchema)
def create_payment(project_id: int, payment: ProjectPaymentCreate, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        return project_service.create_payment(db, project, payment)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create payment")


@router.patch("/{project_id}/payments/{payment_id}", response_model=ProjectPaymentSchema)
def update_payment(
    project_id: int, payment_id: int, payment_update: ProjectPaymentUpdate, db: Session = Depends(get_db)
):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    payment = (
        db.query(ProjectPayment)
        .filter(
            ProjectPayment.id == payment_id,
            ProjectPayment.project_id == project_id,
        )
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    try:
        return project_service.update_payment(db, project, payment, payment_update)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update payment")


@router.delete("/{project_id}/payments/{payment_id}")
def delete_payment(project_id: int, payment_id: int, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    payment = (
        db.query(ProjectPayment)
        .filter(
            ProjectPayment.id == payment_id,
            ProjectPayment.project_id == project_id,
        )
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    project_service.delete_payment(db, project, payment)
    return {"message": "Payment deleted successfully"}


# ---------------------------------------------------------------------------
# Bulk / recurring schedule
# ---------------------------------------------------------------------------


class RecurringScheduleRequest(BaseModel):
    amount: float = Field(gt=0)
    start_date: date
    interval: str  # "weekly", "biweekly", "monthly"
    occurrences: int = Field(gt=0, le=120)
    notes: Optional[str] = None


@router.post("/{project_id}/payments/bulk", response_model=List[ProjectPaymentSchema])
def bulk_create_payments(project_id: int, req: RecurringScheduleRequest, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project_service.bulk_create_payments(db, project, req)


# ---------------------------------------------------------------------------
# Link savings to project
# ---------------------------------------------------------------------------


@router.post("/{project_id}/link-savings/{savings_id}")
def link_savings_to_project(project_id: int, savings_id: int, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    savings = db.query(SavingsBundle).filter(SavingsBundle.id == savings_id).first()
    if not savings:
        raise HTTPException(status_code=404, detail="Savings bundle not found")
    savings.linked_project_id = project_id
    db.commit()
    return {"message": "Savings bundle linked to project successfully"}
