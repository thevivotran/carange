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


def _get_or_create_interest_category(db: Session) -> Category | None:
    cat = (
        db.query(Category)
        .filter(
            Category.type == TransactionType.INCOME,
            Category.name == "Lãi tiết kiệm",
        )
        .first()
    )
    if cat:
        return cat
    cat = Category(
        name="Lãi tiết kiệm",
        type=TransactionType.INCOME,
        color="#f59e0b",
        icon="piggy-bank",
        is_active=True,
        is_wealth_building=False,
    )
    db.add(cat)
    db.flush()
    return cat


def mark_bundle_completed(db: Session, bundle_id: int) -> SavingsBundle:
    """Mark a bundle as completed.

    Creates two transactions:
    - Principal return (is_savings_related=True) — excluded from income KPIs.
    - Interest earned (is_savings_related=False, "Lãi tiết kiệm" category) — flows into KPIs.
      Skipped when future_amount <= initial_deposit.
    """
    bundle = db.query(SavingsBundle).filter(SavingsBundle.id == bundle_id, SavingsBundle.deleted_at.is_(None)).first()
    if bundle is None:
        raise LookupError("Savings bundle not found")
    if bundle.status != SavingsStatus.ACTIVE:
        raise ValueError("Only active bundles can be marked as completed")

    try:
        bundle.status = SavingsStatus.COMPLETED
        bundle.completed_at = datetime.now(timezone.utc)

        fallback_cat = (
            db.query(Category).filter(Category.type == TransactionType.INCOME, Category.name == "Investment").first()
            or db.query(Category).filter(Category.type == TransactionType.INCOME).first()
        )
        if fallback_cat is None:
            db.commit()
            db.refresh(bundle)
            return bundle

        today = date.today()

        # Principal return — excluded from KPIs
        db.add(
            Transaction(
                date=today,
                amount=bundle.initial_deposit,
                type=TransactionType.INCOME,
                category_id=fallback_cat.id,
                description=f"Principal returned: {bundle.name} - {bundle.bank_name}",
                payment_method="bank",
                is_savings_related=True,
                savings_bundle_id=bundle.id,
                source="savings_maturity",
                needs_review=False,
            )
        )

        # Interest income — counted in KPIs
        interest = bundle.future_amount - bundle.initial_deposit
        if interest > 0:
            interest_cat = _get_or_create_interest_category(db) or fallback_cat
            db.add(
                Transaction(
                    date=today,
                    amount=interest,
                    type=TransactionType.INCOME,
                    category_id=interest_cat.id,
                    description=f"Interest earned: {bundle.name} - {bundle.bank_name}",
                    payment_method="bank",
                    is_savings_related=False,
                    savings_bundle_id=bundle.id,
                    source="savings_maturity",
                    needs_review=True,
                )
            )

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
