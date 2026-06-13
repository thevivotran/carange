"""Financial project and payment domain business logic."""

import calendar
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.database import (
    FinancialProject,
    PaymentStatus,
    ProjectPayment,
    ProjectStatus,
    Transaction,
    TransactionType,
)


def recompute_project_totals(db: Session, project: FinancialProject) -> None:
    """Recompute target_amount and current_amount from payments. Flushes; caller commits."""
    db.flush()
    totals = (
        db.query(
            func.sum(ProjectPayment.amount).label("target"),
            func.sum(case((ProjectPayment.status == PaymentStatus.PAID, ProjectPayment.amount), else_=0)).label(
                "current"
            ),
        )
        .filter(ProjectPayment.project_id == project.id)
        .one()
    )
    project.target_amount = float(totals.target or 0)
    project.current_amount = float(totals.current or 0)
    if project.current_amount > 0 and project.status == ProjectStatus.PLANNING:
        project.status = ProjectStatus.IN_PROGRESS
    db.flush()


def calc_progress(project: FinancialProject) -> float:
    if project.target_amount > 0:
        return round((project.current_amount / project.target_amount) * 100, 2)
    return 0.0


def next_date(d: date, interval: str) -> date:
    if interval == "weekly":
        return d + timedelta(weeks=1)
    if interval == "biweekly":
        return d + timedelta(weeks=2)
    if interval == "monthly":
        month = d.month + 1
        year = d.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        day = min(d.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)
    raise ValueError(f"Invalid interval: {interval}")


def create_payment(db: Session, project: FinancialProject, payment_data) -> ProjectPayment:
    """Create a single payment and recompute project totals atomically."""
    try:
        db_payment = ProjectPayment(project_id=project.id, **payment_data.model_dump())
        db.add(db_payment)
        db.flush()
        recompute_project_totals(db, project)
        db.commit()
        db.refresh(db_payment)
        return db_payment
    except Exception:
        db.rollback()
        raise


def mark_payment_paid(
    db: Session,
    project: FinancialProject,
    payment: ProjectPayment,
    category_id: int,
    payment_date: date | None,
) -> ProjectPayment:
    """Create an expense transaction and link it to the payment atomically."""
    try:
        paid_date = payment_date or payment.due_date or date.today()
        tx = Transaction(
            date=paid_date,
            amount=payment.amount,
            type=TransactionType.EXPENSE,
            category_id=category_id,
            description=f"[{project.name}] {payment.notes or ''}".strip(),
            project_id=project.id,
            source="project_payment",
            needs_review=True,
        )
        db.add(tx)
        db.flush()
        payment.transaction_id = tx.id
        payment.status = PaymentStatus.PAID
        recompute_project_totals(db, project)
        db.commit()
        db.refresh(payment)
        return payment
    except Exception:
        db.rollback()
        raise


def settle_payment_from_transaction(db, project, payment, tx):
    """Link an existing transaction to a PENDING payment and mark it PAID."""
    try:
        payment.transaction_id = tx.id
        payment.status = PaymentStatus.PAID
        if tx.project_id is None:
            tx.project_id = project.id
        recompute_project_totals(db, project)
        db.commit()
        db.refresh(payment)
        return payment
    except Exception:
        db.rollback()
        raise


def update_payment(db: Session, project: FinancialProject, payment: ProjectPayment, payment_update) -> ProjectPayment:
    """Apply payment update; auto-create transaction when marking PAID."""
    try:
        update_data = payment_update.model_dump(exclude_unset=True)
        category_id = update_data.pop("category_id", None)
        payment_date = update_data.pop("payment_date", None)

        for key, value in update_data.items():
            setattr(payment, key, value)

        if update_data.get("status") == PaymentStatus.PAID and category_id and payment.transaction_id is None:
            return mark_payment_paid(db, project, payment, category_id, payment_date)

        db.flush()
        recompute_project_totals(db, project)
        db.commit()
        db.refresh(payment)
        return payment
    except Exception:
        db.rollback()
        raise


def delete_payment(db: Session, project: FinancialProject, payment: ProjectPayment) -> None:
    """Delete a payment and recompute project totals atomically."""
    try:
        db.delete(payment)
        db.flush()
        recompute_project_totals(db, project)
        db.commit()
    except Exception:
        db.rollback()
        raise


def bulk_create_payments(db: Session, project: FinancialProject, req) -> list[ProjectPayment]:
    """Create a recurring payment schedule and recompute project totals atomically."""
    try:
        created = []
        current = req.start_date
        for _ in range(req.occurrences):
            p = ProjectPayment(
                project_id=project.id,
                due_date=current,
                amount=req.amount,
                status=PaymentStatus.PENDING,
                notes=req.notes,
            )
            db.add(p)
            created.append(p)
            current = next_date(current, req.interval)
        db.flush()
        recompute_project_totals(db, project)
        db.commit()
        for p in created:
            db.refresh(p)
        return created
    except Exception:
        db.rollback()
        raise


def soft_delete_project(db: Session, project_id: int) -> None:
    project = (
        db.query(FinancialProject)
        .filter(FinancialProject.id == project_id, FinancialProject.deleted_at.is_(None))
        .first()
    )
    if project is None:
        raise LookupError("Project not found")
    try:
        project.deleted_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise


def restore_project(db: Session, project_id: int) -> FinancialProject:
    project = (
        db.query(FinancialProject)
        .filter(FinancialProject.id == project_id, FinancialProject.deleted_at.isnot(None))
        .first()
    )
    if project is None:
        raise LookupError("Project not found in trash")
    try:
        project.deleted_at = None
        db.commit()
        db.refresh(project)
        return project
    except Exception:
        db.rollback()
        raise


def hard_delete_project(db: Session, project_id: int) -> None:
    project = (
        db.query(FinancialProject)
        .filter(FinancialProject.id == project_id, FinancialProject.deleted_at.isnot(None))
        .first()
    )
    if project is None:
        raise LookupError("Project not found in trash")
    try:
        db.delete(project)
        db.commit()
    except Exception:
        db.rollback()
        raise


def get_trashed_projects(db: Session, skip: int = 0, limit: int = 100) -> list[FinancialProject]:
    return (
        db.query(FinancialProject)
        .filter(FinancialProject.deleted_at.isnot(None))
        .order_by(FinancialProject.deleted_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
