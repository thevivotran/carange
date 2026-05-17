"""Savings bundle domain business logic."""

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.database import (
    Category,
    SavingsBundle,
    SavingsStatus,
    Transaction,
    TransactionType,
)


def mark_bundle_completed(db: Session, bundle_id: int) -> SavingsBundle:
    """Mark a bundle as completed and create a maturity income transaction."""
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id, SavingsBundle.deleted_at.is_(None)).first()
    if bundle is None:
        raise LookupError("Savings bundle not found")
    if bundle.status != SavingsStatus.ACTIVE:
        raise ValueError("Only active bundles can be marked as completed")

    try:
        bundle.status = SavingsStatus.COMPLETED
        bundle.completed_at = datetime.now(timezone.utc)

        category = (
            db.query(Category).filter(Category.type == TransactionType.INCOME, Category.name == "Investment").first()
            or db.query(Category).filter(Category.type == TransactionType.INCOME).first()
        )
        if category:
            tx = Transaction(
                date=date.today(),
                amount=bundle.future_amount,
                type=TransactionType.INCOME,
                category_id=category.id,
                description=f"Savings matured: {bundle.name} - {bundle.bank_name}",
                payment_method="bank",
                is_savings_related=True,
                savings_bundle_id=bundle.id,
                source="savings_maturity",
                needs_review=True,
            )
            db.add(tx)

        db.commit()
        db.refresh(bundle)
        return bundle
    except Exception:
        db.rollback()
        raise


def rollover_bundle(db: Session, bundle_id: int) -> SavingsBundle:
    """Complete an active bundle and create a new one seeded with the matured amount."""
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id, SavingsBundle.deleted_at.is_(None)).first()
    if bundle is None:
        raise LookupError("Savings bundle not found")
    if bundle.status != SavingsStatus.ACTIVE:
        raise ValueError("Only active bundles can be rolled over")

    try:
        bundle.status = SavingsStatus.COMPLETED
        bundle.completed_at = datetime.now(timezone.utc)

        today = date.today()
        original_term_days = (bundle.maturity_date - bundle.start_date).days if bundle.maturity_date else None
        new_maturity = date.fromordinal(today.toordinal() + original_term_days) if original_term_days else None

        new_bundle = SavingsBundle(
            name=f"{bundle.name} (Rollover)",
            bank_name=bundle.bank_name,
            type=bundle.type,
            initial_deposit=bundle.future_amount,
            current_amount=bundle.future_amount,
            future_amount=bundle.future_amount,
            interest_rate=bundle.interest_rate,
            start_date=today,
            maturity_date=new_maturity,
            status=SavingsStatus.ACTIVE,
            notes=f"Rolled over from bundle #{bundle.id}",
        )
        db.add(new_bundle)
        db.commit()
        db.refresh(new_bundle)
        return new_bundle
    except Exception:
        db.rollback()
        raise


def soft_delete_bundle(db: Session, bundle_id: int) -> None:
    """Soft-delete a savings bundle."""
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id, SavingsBundle.deleted_at.is_(None)).first()
    if bundle is None:
        raise LookupError("Savings bundle not found")
    try:
        bundle.deleted_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        raise


def restore_bundle(db: Session, bundle_id: int) -> SavingsBundle:
    """Restore a soft-deleted savings bundle."""
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id, SavingsBundle.deleted_at.isnot(None)).first()
    if bundle is None:
        raise LookupError("Savings bundle not found in trash")
    try:
        bundle.deleted_at = None
        db.commit()
        db.refresh(bundle)
        return bundle
    except Exception:
        db.rollback()
        raise


def hard_delete_bundle(db: Session, bundle_id: int) -> None:
    """Permanently delete a soft-deleted savings bundle."""
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id, SavingsBundle.deleted_at.isnot(None)).first()
    if bundle is None:
        raise LookupError("Savings bundle not found in trash")
    try:
        db.query(Transaction).filter(Transaction.savings_bundle_id == bundle_id).update({"savings_bundle_id": None})
        db.delete(bundle)
        db.commit()
    except Exception:
        db.rollback()
        raise


def get_trashed_bundles(db: Session, skip: int = 0, limit: int = 100) -> list[SavingsBundle]:
    return (
        db.query(SavingsBundle)
        .filter(SavingsBundle.deleted_at.isnot(None))
        .order_by(SavingsBundle.deleted_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
