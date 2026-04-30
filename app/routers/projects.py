from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.models.database import (
    get_db, FinancialProject, ProjectMilestone, ProjectContribution,
    ProjectStatus, ProjectType, SavingsBundle, SavingsStatus,
    ProjectPayment, PaymentStatus, Transaction, TransactionType, Category,
)
from app.models.schemas import (
    FinancialProject as FinancialProjectSchema,
    FinancialProjectCreate,
    FinancialProjectUpdate,
    ProjectMilestone as ProjectMilestoneSchema,
    ProjectMilestoneCreate,
    ProjectContribution as ProjectContributionSchema,
    ProjectContributionCreate,
    ProjectPayment as ProjectPaymentSchema,
    ProjectPaymentCreate,
    ProjectPaymentUpdate,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _recompute_project_totals(db: Session, project) -> None:
    """Recompute target_amount and current_amount from payments. Flushes; caller commits."""
    payments = db.query(ProjectPayment).filter(ProjectPayment.project_id == project.id).all()
    project.target_amount  = sum(p.amount for p in payments)
    project.current_amount = sum(p.amount for p in payments if p.status == PaymentStatus.PAID)
    if project.current_amount > 0 and project.status == ProjectStatus.PLANNING:
        project.status = ProjectStatus.IN_PROGRESS
    db.flush()


def _calc_progress(project) -> float:
    if project.target_amount > 0:
        return round((project.current_amount / project.target_amount) * 100, 2)
    return 0.0


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------

@router.get("/", response_model=List[FinancialProjectSchema])
def get_projects(
    status: Optional[ProjectStatus] = None,
    project_type: Optional[ProjectType] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(FinancialProject)
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
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
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


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
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
            ProjectPayment.due_date.is_(None),   # False < True → dated rows first
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

    db_payment = ProjectPayment(project_id=project_id, **payment.model_dump())
    db.add(db_payment)
    db.flush()
    _recompute_project_totals(db, project)
    db.commit()
    db.refresh(db_payment)
    return db_payment


@router.patch("/{project_id}/payments/{payment_id}", response_model=ProjectPaymentSchema)
def update_payment(project_id: int, payment_id: int, payment_update: ProjectPaymentUpdate, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    payment = db.query(ProjectPayment).filter(
        ProjectPayment.id == payment_id,
        ProjectPayment.project_id == project_id,
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    update_data = payment_update.model_dump(exclude_unset=True)

    # Extract extra fields not on the ORM model
    category_id  = update_data.pop("category_id", None)
    payment_date = update_data.pop("payment_date", None)

    # Apply core field updates
    for key, value in update_data.items():
        setattr(payment, key, value)

    # Auto-create transaction when marking paid
    if update_data.get("status") == PaymentStatus.PAID and category_id and payment.transaction_id is None:
        paid_date = payment_date or payment.due_date or date.today()
        tx = Transaction(
            date=paid_date,
            amount=payment.amount,
            type=TransactionType.EXPENSE,
            category_id=category_id,
            description=f"[{project.name}] {payment.notes or ''}".strip(),
            project_id=project_id,
        )
        db.add(tx)
        db.flush()
        payment.transaction_id = tx.id

    db.flush()
    _recompute_project_totals(db, project)
    db.commit()
    db.refresh(payment)
    return payment


@router.delete("/{project_id}/payments/{payment_id}")
def delete_payment(project_id: int, payment_id: int, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    payment = db.query(ProjectPayment).filter(
        ProjectPayment.id == payment_id,
        ProjectPayment.project_id == project_id,
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    db.delete(payment)
    db.flush()
    _recompute_project_totals(db, project)
    db.commit()
    return {"message": "Payment deleted successfully"}


# ---------------------------------------------------------------------------
# Bulk / recurring schedule
# ---------------------------------------------------------------------------

class RecurringScheduleRequest(BaseModel):
    amount:      float = Field(gt=0)
    start_date:  date
    interval:    str   # "weekly", "biweekly", "monthly"
    occurrences: int = Field(gt=0, le=120)
    notes:       Optional[str] = None


def _next_date(d: date, interval: str) -> date:
    if interval == "weekly":
        return d + timedelta(weeks=1)
    if interval == "biweekly":
        return d + timedelta(weeks=2)
    if interval == "monthly":
        month = d.month + 1
        year  = d.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        # Clamp day for short months
        import calendar
        day = min(d.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    raise ValueError(f"Invalid interval: {interval}")


@router.post("/{project_id}/payments/bulk", response_model=List[ProjectPaymentSchema])
def bulk_create_payments(project_id: int, req: RecurringScheduleRequest, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    created = []
    current_date = req.start_date
    for _ in range(req.occurrences):
        p = ProjectPayment(
            project_id=project_id,
            due_date=current_date,
            amount=req.amount,
            status=PaymentStatus.PENDING,
            notes=req.notes,
        )
        db.add(p)
        created.append(p)
        current_date = _next_date(current_date, req.interval)

    db.flush()
    _recompute_project_totals(db, project)
    db.commit()
    for p in created:
        db.refresh(p)
    return created


# ---------------------------------------------------------------------------
# Milestone Routes (kept for back-compat)
# ---------------------------------------------------------------------------

@router.get("/{project_id}/milestones")
def get_milestones(project_id: int, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return db.query(ProjectMilestone).filter(
        ProjectMilestone.project_id == project_id
    ).order_by(ProjectMilestone.target_amount).all()


@router.post("/{project_id}/milestones")
def add_milestone(project_id: int, milestone: ProjectMilestoneCreate, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    db_milestone = ProjectMilestone(
        project_id=project_id,
        name=milestone.name,
        target_amount=milestone.target_amount,
        is_completed=milestone.is_completed,
    )
    db.add(db_milestone)
    db.commit()
    db.refresh(db_milestone)
    return db_milestone


@router.patch("/{project_id}/milestones/{milestone_id}/complete")
def complete_milestone(project_id: int, milestone_id: int, db: Session = Depends(get_db)):
    milestone = db.query(ProjectMilestone).filter(
        ProjectMilestone.id == milestone_id,
        ProjectMilestone.project_id == project_id,
    ).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    milestone.is_completed = True
    milestone.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Milestone marked as completed"}


@router.delete("/{project_id}/milestones/{milestone_id}")
def delete_milestone(project_id: int, milestone_id: int, db: Session = Depends(get_db)):
    milestone = db.query(ProjectMilestone).filter(
        ProjectMilestone.id == milestone_id,
        ProjectMilestone.project_id == project_id,
    ).first()
    if not milestone:
        raise HTTPException(status_code=404, detail="Milestone not found")
    db.delete(milestone)
    db.commit()
    return {"message": "Milestone deleted"}


# ---------------------------------------------------------------------------
# Contribution Routes (kept for back-compat)
# ---------------------------------------------------------------------------

@router.get("/{project_id}/contributions")
def get_contributions(project_id: int, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return db.query(ProjectContribution).filter(
        ProjectContribution.project_id == project_id
    ).order_by(ProjectContribution.date.desc()).all()


@router.post("/{project_id}/contribute")
def add_contribution(project_id: int, contribution: ProjectContributionCreate, db: Session = Depends(get_db)):
    project = db.query(FinancialProject).filter(FinancialProject.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if contribution.source == "savings" and contribution.savings_bundle_id:
        savings = db.query(SavingsBundle).filter(SavingsBundle.id == contribution.savings_bundle_id).first()
        if not savings:
            raise HTTPException(status_code=404, detail="Savings bundle not found")
        if savings.current_amount < contribution.amount:
            raise HTTPException(status_code=400, detail="Insufficient funds in savings bundle")
        savings.current_amount -= contribution.amount

    db_contribution = ProjectContribution(
        project_id=project_id,
        amount=contribution.amount,
        date=contribution.date,
        source=contribution.source,
        savings_bundle_id=contribution.savings_bundle_id,
        notes=contribution.notes,
    )
    db.add(db_contribution)
    project.current_amount += contribution.amount
    if project.status == ProjectStatus.PLANNING and project.current_amount > 0:
        project.status = ProjectStatus.IN_PROGRESS
    db.commit()

    return {
        "message": "Contribution added successfully",
        "new_total": project.current_amount,
        "progress_percentage": round((project.current_amount / project.target_amount * 100), 2) if project.target_amount > 0 else 0,
    }


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


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats/summary")
def get_projects_summary(db: Session = Depends(get_db)):
    active_projects = db.query(FinancialProject).filter(
        FinancialProject.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS])
    ).all()

    total_target  = sum(p.target_amount for p in active_projects)
    total_current = sum(p.current_amount for p in active_projects)

    completed_count = db.query(FinancialProject).filter(
        FinancialProject.status == ProjectStatus.COMPLETED
    ).count()

    return {
        "active_projects_count": len(active_projects),
        "completed_projects_count": completed_count,
        "total_target_amount": total_target,
        "total_current_amount": total_current,
        "progress_percentage": round((total_current / total_target * 100), 2) if total_target > 0 else 0,
    }
