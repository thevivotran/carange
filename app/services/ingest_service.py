"""Shared transaction ingest pipeline.

All automatic ingestion paths (OCR worker, email worker) call
commit_ingest_batch() so dedup, rule application, and anomaly detection
are consistent regardless of source.
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database import Category, Transaction, TransactionType
from app.services import ollama as _ollama
from app.services.rules_service import RuleAction, apply_rules, normalize_description

log = logging.getLogger("app.ingest_service")

REVIEW_THRESHOLD = float(os.getenv("REVIEW_THRESHOLD", "0.95"))
ANOMALY_MULTIPLIER = float(os.getenv("ANOMALY_MULTIPLIER", "3.0"))
ANOMALY_MIN_SAMPLES = int(os.getenv("ANOMALY_MIN_SAMPLES", "3"))


@dataclass
class IngestItem:
    date: date
    amount: float
    tx_type: str  # "expense" | "income"
    description: Optional[str]
    confidence: float
    category_hint: Optional[str] = None
    payment_method: str = "bank_transfer"
    extra: dict = field(default_factory=dict)  # pass-through fields for future use


def commit_ingest_batch(
    db: Session,
    items: list[IngestItem],
    source_tag: str,
    import_job_id: Optional[int] = None,
    email_ingest_log_id: Optional[int] = None,
) -> list[Transaction]:
    """Dedup, normalize, apply rules, detect anomalies, then persist.

    All items are committed in one transaction at the end.
    Returns the list of Transaction objects that were created.
    """
    committed: list[Transaction] = []

    for item in items:
        if _is_duplicate(db, item):
            log.debug("Duplicate skipped: %s %s %.0f", item.date, item.description, item.amount)
            continue

        category_id = _resolve_category(db, item)
        if category_id is None:
            log.warning("No category for '%s' — skipping", item.description)
            continue

        # Resolve payee_id without touching the description
        _, payee_id = normalize_description(db, item.description or "")

        tx = Transaction(
            date=item.date,
            amount=item.amount,
            type=TransactionType(item.tx_type),
            category_id=category_id,
            description=item.description,
            payment_method=item.payment_method,
            source=source_tag,
            import_job_id=import_job_id,
            email_ingest_log_id=email_ingest_log_id,
            payee_id=payee_id,
            confidence_score=item.confidence,
            needs_review=item.confidence < REVIEW_THRESHOLD,
        )
        db.add(tx)
        db.flush()  # populate tx.id so apply_rules can reference it

        action: RuleAction = apply_rules(db, tx, payee_id)
        if action.category_id is not None:
            tx.category_id = action.category_id
        if action.auto_approve:
            tx.needs_review = False
        if action.force_needs_review:
            tx.needs_review = True

        if _is_anomaly(db, item, tx.category_id):
            tx.needs_review = True
            log.info("Anomaly: '%s' %.0f VND — flagged for review", item.description, item.amount)

        committed.append(tx)

    # Publish tx_ingested events inside the same transaction so they are committed
    # atomically with the rows. Without this, the transaction is visible in the UI
    # before the notification event is queued, which lets a concurrent advance_ping
    # (from the user editing the new row) land in the worker queue first.
    if committed:
        from app.services.notification_service import publish_notification

        for tx in committed:
            try:
                with db.begin_nested():  # SAVEPOINT: isolate each notification flush
                    publish_notification(
                        db,
                        "tx_ingested",
                        {
                            "tx_id": tx.id,
                            "amount": str(tx.amount),
                            "tx_type": tx.type.value if tx.type else "expense",
                            "source": tx.source or "manual",
                            "cat_name": tx.category.name if tx.category else "?",
                            "description": tx.description or "No description",
                            "needs_review": tx.needs_review or False,
                            "category_id": tx.category_id,
                            "date": str(tx.date) if tx.date else None,
                        },
                    )
            except Exception:
                log.warning("Failed to publish tx_ingested notification for tx %d", tx.id, exc_info=True)

    db.commit()  # transactions + notification events committed atomically

    # Cross-pod cache invalidation: workers run in separate pods from the main app.
    # Writing the sentinel here ensures the main app recomputes on the next request.
    if committed:
        from app.services.dashboard_service import invalidate_dashboard_cache

        invalidate_dashboard_cache(db)

    return committed


# ── Private helpers ────────────────────────────────────────────────────────────


def _is_duplicate(db: Session, item: IngestItem) -> bool:
    """Loose dedup on (date, amount, type). Catches re-uploads and email re-forwards."""
    return (
        db.query(Transaction)
        .filter(
            Transaction.date == item.date,
            Transaction.amount == item.amount,
            Transaction.type == TransactionType(item.tx_type),
            Transaction.deleted_at.is_(None),
        )
        .first()
        is not None
    )


def _is_anomaly(db: Session, item: IngestItem, category_id: int) -> bool:
    """True if amount exceeds ANOMALY_MULTIPLIER × 90-day category average.

    Requires ANOMALY_MIN_SAMPLES prior rows to establish a baseline.
    """
    cutoff = item.date - timedelta(days=90)
    row = (
        db.query(
            func.count(Transaction.id).label("n"),
            func.avg(Transaction.amount).label("avg"),
        )
        .filter(
            Transaction.category_id == category_id,
            Transaction.type == TransactionType(item.tx_type),
            Transaction.date >= cutoff,
            Transaction.deleted_at.is_(None),
        )
        .first()
    )
    if not row or row.n < ANOMALY_MIN_SAMPLES or not row.avg:
        return False
    return item.amount > float(row.avg) * ANOMALY_MULTIPLIER


def _resolve_category(db: Session, item: IngestItem) -> Optional[int]:
    """Resolve category: hint match → Ollama LLM → keyword fallback → first active."""
    tx_type = TransactionType(item.tx_type)
    active = db.query(Category).filter(Category.type == tx_type, Category.is_active == True).all()
    if not active:
        return None

    # 1. Hint
    if item.category_hint:
        for cat in active:
            if item.category_hint.lower() in cat.name.lower():
                return cat.id

    # 2. LLM
    if _ollama.is_enabled() and item.description:
        cat_names = ", ".join(c.name for c in active)
        result = _ollama.generate_sync(
            prompt=(
                f"Transaction type: {item.tx_type}\n"
                f"Description: {item.description}\n"
                f"Amount: {item.amount:.0f} VND\n\n"
                f"Available categories: {cat_names}\n\n"
                "Reply with ONLY the category name that best fits. No explanation."
            ),
            system=(
                "You are a Vietnamese personal finance assistant. "
                "Categorize transactions accurately. "
                "Reply with exactly one category name from the provided list."
            ),
        )
        if result:
            for cat in active:
                if cat.name.lower() == result.strip().lower():
                    return cat.id
            for cat in active:
                if cat.name.lower() in result.lower():
                    return cat.id

    # 3. Keyword fallback → "Others"
    for cat in active:
        if "khác" in cat.name.lower() or "others" in cat.name.lower():
            return cat.id

    return active[0].id
