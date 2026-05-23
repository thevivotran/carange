from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from typing import Optional
from datetime import date

from app.models.database import (
    get_db,
    Transaction,
    TransactionAuditLog,
)
from app.routers.fragments._helpers import render_fragment

router = APIRouter()

OCR_SOURCES = {"timo", "uob", "liobank", "shopee", "grab", "ocr"}


def _build_tx_query(
    db: Session,
    skip: int = 0,
    limit: int = 20,
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
    trash: bool = False,
):
    if trash:
        query = db.query(Transaction).filter(Transaction.deleted_at.isnot(None))
        return query.order_by(Transaction.deleted_at.desc()).offset(skip).limit(limit).all()

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

    return query.order_by(Transaction.date.desc()).offset(skip).limit(limit).all()


@router.get("/list")
def fragment_transaction_list(
    request: Request,
    skip: int = 0,
    limit: int = 20,
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
    trash: bool = False,
    db: Session = Depends(get_db),
):
    transactions = _build_tx_query(
        db,
        skip=skip,
        limit=limit,
        type=type,
        category_id=category_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        search=search,
        is_advance=is_advance,
        advance_settled=advance_settled,
        source=source,
        needs_review=needs_review,
        import_job_id=import_job_id,
        trash=trash,
    )

    # Group by date for the template
    date_groups: dict = {}
    date_order: list = []
    total_income = 0
    total_expense = 0
    for t in transactions:
        d = str(t.date)
        if d not in date_groups:
            date_groups[d] = []
            date_order.append(d)
        date_groups[d].append(t)
        if t.type == "income":
            total_income += t.amount
        else:
            total_expense += t.amount

    # Compute daily net per group
    day_nets = {}
    for d, txs in date_groups.items():
        inc = sum(t.amount for t in txs if t.type == "income")
        exp = sum(t.amount for t in txs if t.type == "expense")
        day_nets[d] = inc - exp

    # Determine which transactions were edited (normalize tz before subtracting)
    edited_ids: set[int] = set()
    for t in transactions:
        if t.updated_at and t.created_at:
            try:
                ua = t.updated_at.replace(tzinfo=None)
                ca = t.created_at.replace(tzinfo=None)
                if (ua - ca).total_seconds() > 2:
                    edited_ids.add(t.id)
            except Exception:
                pass

    current_page = skip // limit if limit else 0

    return render_fragment(
        request,
        "partials/transactions/_list_body.html",
        {
            "transactions": transactions,
            "date_groups": date_groups,
            "date_order": date_order,
            "day_nets": day_nets,
            "total_income": total_income,
            "total_expense": total_expense,
            "total_net": total_income - total_expense,
            "current_page": current_page,
            "page_size": limit,
            "has_prev": current_page > 0,
            "has_next": len(transactions) >= limit,
            "trash": trash,
            "ocr_sources": OCR_SOURCES,
            "edited_ids": edited_ids,
        },
    )


@router.get("/summary")
def fragment_monthly_summary(
    request: Request,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    from app.routers.transactions import get_monthly_summary

    if start_date:
        data = get_monthly_summary(year=start_date.year, month=start_date.month, db=db)
    else:
        data = get_monthly_summary(db=db)
    return render_fragment(
        request,
        "partials/transactions/_monthly_summary.html",
        {"summary": data},
    )


@router.get("/{transaction_id}/history")
def fragment_history(
    transaction_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    logs = (
        db.query(TransactionAuditLog)
        .filter(TransactionAuditLog.transaction_id == transaction_id)
        .order_by(TransactionAuditLog.changed_at.desc())
        .all()
    )
    return render_fragment(
        request,
        "partials/transactions/_history_content.html",
        {"logs": logs},
    )
