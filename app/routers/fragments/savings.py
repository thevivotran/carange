from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, func
from sqlalchemy.orm import Session, joinedload

from app.models.database import SavingsBundle, SavingsStatus, SavingsType, Transaction, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


@router.get("/grid")
def fragment_savings_grid(
    request: Request,
    status: str = "active",
    db: Session = Depends(get_db),
):
    query = (
        db.query(SavingsBundle, func.count(Transaction.id).label("tx_count"))
        .outerjoin(
            Transaction,
            and_(Transaction.savings_bundle_id == SavingsBundle.id, Transaction.deleted_at.is_(None)),
        )
        .filter(SavingsBundle.deleted_at.is_(None))
        .group_by(SavingsBundle.id)
    )
    if status:
        query = query.filter(SavingsBundle.status == status)

    bundles = []
    for bundle, count in query.order_by(SavingsBundle.created_at.desc()).all():
        bundle.linked_transaction_count = count
        bundles.append(bundle)

    active_bundles = (
        db.query(SavingsBundle)
        .filter(
            SavingsBundle.status == SavingsStatus.ACTIVE,
            SavingsBundle.deleted_at.is_(None),
        )
        .all()
    )
    total_saved = sum(b.current_amount if b.current_amount is not None else b.initial_deposit for b in active_bundles)
    total_future = sum(b.future_amount for b in active_bundles)
    # Only deposits earn a contractual interest figure — a savings goal's future_amount
    # is a target, so (target − deposit) is money still to save, not interest.
    total_interest = sum(
        b.future_amount - b.initial_deposit for b in active_bundles if b.type != SavingsType.SAVINGS_GOAL
    )

    return render_fragment(
        request,
        "partials/savings/_bundle_grid.html",
        {
            "bundles": bundles,
            "status_filter": status,
            "active_count": len(active_bundles),
            "total_saved": total_saved,
            "total_future": total_future,
            "total_interest": total_interest,
            "today": date.today(),
        },
    )


@router.get("/{bundle_id}/transactions")
def fragment_bundle_transactions(
    request: Request,
    bundle_id: int,
    db: Session = Depends(get_db),
):
    txs = (
        db.query(Transaction)
        .options(joinedload(Transaction.category))
        .filter(Transaction.savings_bundle_id == bundle_id, Transaction.deleted_at.is_(None))
        .order_by(Transaction.date.desc())
        .all()
    )
    return render_fragment(request, "partials/savings/_tx_list.html", {"txs": txs})
