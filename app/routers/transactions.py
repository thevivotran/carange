from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, case, and_
from typing import List, Optional
from datetime import date, datetime, timezone

from app.models.database import (
    get_db,
    Transaction,
    TransactionAuditLog,
    AuditField,
    Category,
    TransactionType,
    SavingsBundle,
    SavingsStatus,
    PaymentStatus,
    ProjectPayment,
    FinancialProject,
)
from app.models.schemas import (
    Transaction as TransactionSchema,
    TransactionCreate,
    TransactionUpdate,
    TransactionAuditLogEntry,
    ProjectPayment as ProjectPaymentSchema,
)
from app.services import transaction_service, project_service
from app.services.dashboard_service import invalidate_dashboard_cache
from app.services.fiscal_period import current_period_ym, fiscal_window_ym, get_month_start_day
from app.notify.telegram import send_personal_advance_ping

_AUDIT_FIELDS = list(AuditField)

router = APIRouter()


@router.get("/", response_model=List[TransactionSchema])
def get_transactions(
    skip: int = 0,
    limit: int = 100,
    type: Optional[str] = None,
    category_id: Optional[int] = None,
    project_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    search: Optional[str] = None,
    is_advance: Optional[bool] = None,
    advance_settled: Optional[bool] = None,
    source: Optional[str] = None,
    needs_review: Optional[bool] = None,
    import_job_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    # Validate date range
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="Start date cannot be after end date")

    query = db.query(Transaction).filter(Transaction.deleted_at.is_(None))

    if type:
        query = query.filter(Transaction.type == type)
    if category_id:
        query = query.filter(Transaction.category_id == category_id)
    if project_id:
        query = query.filter(Transaction.project_id == project_id)
    if start_date:
        query = query.filter(Transaction.date >= start_date)
    if end_date:
        query = query.filter(Transaction.date <= end_date)
    if search:
        query = query.filter(Transaction.description.ilike(f"%{search}%"))
    if is_advance is not None:
        query = query.filter(Transaction.is_advance == is_advance)
    if advance_settled is not None:
        query = query.filter(Transaction.advance_settled == advance_settled)
    if source:
        query = query.filter(Transaction.source == source)
    if needs_review is not None:
        query = query.filter(Transaction.needs_review == needs_review)
    if import_job_id is not None:
        query = query.filter(Transaction.import_job_id == import_job_id)

    return query.order_by(Transaction.date.desc(), Transaction.id.desc()).offset(skip).limit(limit).all()


@router.get("/trash", response_model=List[TransactionSchema])
def get_trashed_transactions(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    return (
        db.query(Transaction)
        .filter(Transaction.deleted_at.isnot(None))
        .order_by(Transaction.deleted_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{transaction_id}/links")
def get_transaction_links(transaction_id: int, db: Session = Depends(get_db)):
    tx = (
        db.query(Transaction)
        .filter(
            Transaction.id == transaction_id,
            Transaction.deleted_at.is_(None),
        )
        .first()
    )
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    result = {"savings_bundle": None, "project_payment": None}

    if tx.savings_bundle_id:
        bundle = db.query(SavingsBundle).filter(SavingsBundle.id == tx.savings_bundle_id).first()
        if bundle:
            result["savings_bundle"] = {"id": bundle.id, "name": bundle.name, "bank_name": bundle.bank_name}

    payment = db.query(ProjectPayment).filter(ProjectPayment.transaction_id == transaction_id).first()
    if payment:
        project = db.query(FinancialProject).filter(FinancialProject.id == payment.project_id).first()
        result["project_payment"] = {
            "payment_id": payment.id,
            "project_name": project.name if project else "Unknown",
            "amount": payment.amount,
        }

    return result


@router.get("/{transaction_id}/history", response_model=List[TransactionAuditLogEntry])
def get_transaction_history(transaction_id: int, db: Session = Depends(get_db)):
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return (
        db.query(TransactionAuditLog)
        .filter(TransactionAuditLog.transaction_id == transaction_id)
        .order_by(TransactionAuditLog.changed_at.desc())
        .all()
    )


@router.post("/{transaction_id}/settle-payment/{payment_id}", response_model=ProjectPaymentSchema)
def settle_payment(transaction_id: int, payment_id: int, db: Session = Depends(get_db)):
    """Link an existing transaction to a PENDING project payment, marking it PAID."""
    tx = db.query(Transaction).filter(Transaction.id == transaction_id, Transaction.deleted_at.is_(None)).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    payment = db.query(ProjectPayment).filter(ProjectPayment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status == PaymentStatus.PAID:
        raise HTTPException(status_code=400, detail="Payment is already paid")

    project = db.query(FinancialProject).filter(FinancialProject.id == payment.project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return project_service.settle_payment_from_transaction(db, project, payment, tx)


@router.get("/{transaction_id}", response_model=TransactionSchema)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)):
    transaction = (
        db.query(Transaction)
        .filter(
            Transaction.id == transaction_id,
            Transaction.deleted_at.is_(None),
        )
        .first()
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


@router.post("/", response_model=TransactionSchema)
def create_transaction(transaction: TransactionCreate, force: bool = False, db: Session = Depends(get_db)):
    category = db.query(Category).filter(Category.id == transaction.category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    if category.type != transaction.type:
        raise HTTPException(
            status_code=400,
            detail=f"Transaction type '{transaction.type}' does not match category type '{category.type}'",
        )

    if not force:
        similar = transaction_service.check_duplicate(
            db, transaction.date, transaction.amount, transaction.type, transaction.category_id
        )
        if similar:
            return JSONResponse(
                content={
                    "duplicate_warning": True,
                    "matches": [
                        {"id": tx.id, "date": str(tx.date), "amount": float(tx.amount), "description": tx.description}
                        for tx in similar
                    ],
                }
            )

    result = transaction_service.create_transaction(db, transaction)
    invalidate_dashboard_cache()
    if result.is_advance and not result.advance_settled:
        send_personal_advance_ping(result, action="created")
    return result


@router.put("/{transaction_id}", response_model=TransactionSchema)
def update_transaction(transaction_id: int, transaction: TransactionUpdate, db: Session = Depends(get_db)):
    db_transaction = (
        db.query(Transaction).filter(Transaction.id == transaction_id, Transaction.deleted_at.is_(None)).first()
    )
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    update_data = transaction.model_dump(exclude_unset=True, exclude={"savings_bundle"})

    if "category_id" in update_data:
        category = db.query(Category).filter(Category.id == update_data["category_id"]).first()
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")

    # Create a SavingsBundle when upgrading a transaction to savings-related
    becoming_savings = update_data.get("is_savings_related", False) and not db_transaction.savings_bundle_id
    if becoming_savings and transaction.savings_bundle:
        bundle_data = transaction.savings_bundle
        db_bundle = SavingsBundle(
            name=bundle_data.name,
            bank_name=bundle_data.bank_name,
            type=bundle_data.type,
            initial_deposit=bundle_data.initial_deposit,
            current_amount=bundle_data.initial_deposit,
            future_amount=bundle_data.future_amount,
            interest_rate=bundle_data.interest_rate,
            start_date=bundle_data.start_date,
            maturity_date=bundle_data.maturity_date,
            notes=bundle_data.notes,
            status=SavingsStatus.ACTIVE,
        )
        db.add(db_bundle)
        db.flush()
        update_data["savings_bundle_id"] = db_bundle.id

    before = transaction_service.snapshot_audit_fields(db_transaction)

    for key, value in update_data.items():
        setattr(db_transaction, key, value)

    transaction_service.write_audit_log(db, transaction_id, before, db_transaction, datetime.now(timezone.utc))

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(db_transaction)
    invalidate_dashboard_cache()
    if db_transaction.is_advance and not db_transaction.advance_settled:
        send_personal_advance_ping(db_transaction, action="updated")
    return db_transaction


@router.delete("/{transaction_id}/hard")
def hard_delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    """Permanently delete a transaction that is already in the trash."""
    transaction = (
        db.query(Transaction)
        .filter(
            Transaction.id == transaction_id,
            Transaction.deleted_at.isnot(None),
        )
        .first()
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found in trash")
    db.delete(transaction)
    db.commit()
    invalidate_dashboard_cache()
    return {"message": "Transaction permanently deleted"}


@router.post("/{transaction_id}/restore", response_model=TransactionSchema)
def restore_transaction(transaction_id: int, db: Session = Depends(get_db)):
    """Restore a soft-deleted transaction from trash."""
    transaction = (
        db.query(Transaction)
        .filter(
            Transaction.id == transaction_id,
            Transaction.deleted_at.isnot(None),
        )
        .first()
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found in trash")
    transaction.deleted_at = None
    db.commit()
    db.refresh(transaction)
    invalidate_dashboard_cache()
    return transaction


@router.delete("/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    """Soft-delete a transaction. Cascades: reverts linked ProjectPayment to PENDING."""
    try:
        transaction_service.soft_delete_transaction(db, transaction_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Transaction not found")
    invalidate_dashboard_cache()
    return {"message": "Transaction deleted"}


@router.get("/stats/monthly-summary")
def get_monthly_summary(year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)):
    day = get_month_start_day(db)
    _cur_year, _cur_month = current_period_ym(date.today(), day)
    year = year or _cur_year
    month = month or _cur_month

    month_start, month_end = fiscal_window_ym(year, month, day)

    # Single query: all five aggregates via CASE expressions
    row = db.query(
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.INCOME,
                        Transaction.is_savings_related == False,
                        Transaction.date >= month_start,
                        Transaction.date <= month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("income"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.is_savings_related == False,
                        Transaction.date >= month_start,
                        Transaction.date <= month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("expense"),
        func.sum(
            case(
                (
                    and_(
                        Transaction.type == TransactionType.EXPENSE,
                        Transaction.is_savings_related == True,
                        Transaction.date >= month_start,
                        Transaction.date <= month_end,
                        Transaction.deleted_at.is_(None),
                    ),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("savings"),
        func.sum(
            case(
                (
                    and_(Transaction.type == TransactionType.INCOME, Transaction.deleted_at.is_(None)),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("total_income"),
        func.sum(
            case(
                (
                    and_(Transaction.type == TransactionType.EXPENSE, Transaction.deleted_at.is_(None)),
                    Transaction.amount,
                ),
                else_=0,
            )
        ).label("total_expense"),
    ).first()

    income = float(row.income or 0)
    expense = float(row.expense or 0)
    savings = float(row.savings or 0)
    cash_on_hand = float((row.total_income or 0) - (row.total_expense or 0))

    return {
        "year": year,
        "month": month,
        "income": income,
        "expense": expense,
        "savings": savings,
        "net": income - expense - savings,
        "cash_on_hand": cash_on_hand,
    }


@router.get("/stats/by-category")
def get_transactions_by_category(
    type: str, year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)
):
    day = get_month_start_day(db)
    _cur_year, _cur_month = current_period_ym(date.today(), day)
    year = year or _cur_year
    month = month or _cur_month

    month_start, month_end = fiscal_window_ym(year, month, day)

    results = (
        db.query(Category.name, Category.color, func.sum(Transaction.amount).label("total"))
        .join(Transaction)
        .filter(
            Transaction.date >= month_start,
            Transaction.date <= month_end,
            Transaction.type == type,
            Transaction.deleted_at.is_(None),
        )
        .group_by(Category.id)
        .all()
    )

    return [{"category": name, "color": color, "total": total} for name, color, total in results]


@router.post("/bulk-upload")
def bulk_upload_transactions(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Bulk upload transactions from CSV (Vietnamese or English format)."""
    content = file.file.read()
    try:
        decoded_check = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            decoded_check = content.decode("utf-8")
        except Exception:
            return JSONResponse(
                status_code=400, content={"error": "Unable to decode CSV file. Please ensure it's UTF-8 encoded."}
            )

    import csv as _csv
    import io as _io

    reader = _csv.DictReader(_io.StringIO(decoded_check))
    fieldnames = reader.fieldnames
    if not fieldnames:
        return JSONResponse(status_code=400, content={"error": "CSV file is empty or has no headers"})

    is_vietnamese_format = "Năm" in fieldnames or "Tháng" in fieldnames

    try:
        if is_vietnamese_format:
            stats = transaction_service.parse_csv_vietnamese(content, db)
        else:
            stats = transaction_service.parse_csv_english(content, db)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    if stats.get("income", 0) + stats.get("expense", 0) > 0:
        invalidate_dashboard_cache()
    return {
        "success": True,
        "message": f"Successfully imported {stats['income']} income and {stats['expense']} expense transactions",
        "stats": stats,
    }
