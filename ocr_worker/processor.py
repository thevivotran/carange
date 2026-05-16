"""
Job processor — drives the full OCR pipeline for one ImportJob.

Phase 1 skeleton → Phase 2 (now):
  image file → PaddleOCR → source detection → source parser → transactions → DB commit
"""
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.database import (
    Category, ImportJob, ImportJobStatus, ImportSource, Transaction, TransactionType
)
from ocr_worker import ocr as _ocr_mod   # module ref — allows monkeypatching in tests
from ocr_worker.parsers import get_parser
from ocr_worker.source_detector import detect_source
from ocr_worker.types import ParsedTransaction

log = logging.getLogger("ocr_worker.processor")

UPLOAD_DIR        = os.getenv("UPLOAD_DIR", "uploads")
REVIEW_THRESHOLD  = float(os.getenv("REVIEW_THRESHOLD", "0.80"))


def process_job(job: ImportJob, db: Session) -> None:
    log.info("Processing job %d: %s", job.id, job.filename)

    if not os.path.isfile(job.file_path):
        _fail(db, job, f"Image not found on disk: {job.file_path}")
        return

    # ── OCR ───────────────────────────────────────────────────────────────────
    try:
        blocks = _ocr_mod.extract_blocks(job.file_path)
    except Exception as exc:
        _fail(db, job, f"OCR failed: {exc}")
        return

    # ── Source detection (always — even before early-exit on empty blocks) ────
    effective_source = job.source_hint or detect_source(blocks)
    if effective_source != job.detected_source:
        job.detected_source = effective_source
        db.flush()

    log.info("Job %d: detected source = %s", job.id, effective_source)

    if not blocks:
        log.info("Job %d: no text detected — marking done (0 tx)", job.id)
        _done(db, job, transaction_count=0)
        return

    # ── Parse transactions ────────────────────────────────────────────────────
    parser = get_parser(effective_source)
    try:
        parsed: List[ParsedTransaction] = parser.parse(blocks)
    except Exception as exc:
        _fail(db, job, f"Parser error: {exc}")
        return

    log.info("Job %d: parser found %d candidate transactions", job.id, len(parsed))

    # ── Commit transactions ───────────────────────────────────────────────────
    committed = _commit_transactions(db, job, parsed, effective_source)
    _done(db, job, transaction_count=committed)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _commit_transactions(
    db: Session,
    job: ImportJob,
    parsed: List[ParsedTransaction],
    source: Optional[ImportSource],
) -> int:
    committed = 0
    for pt in parsed:
        if _is_duplicate(db, pt):
            log.debug("Skipping duplicate: %s %s %.0f", pt.date, pt.description, pt.amount)
            continue

        category_id = _resolve_category(db, pt)
        if category_id is None:
            log.warning("No category found for '%s' — skipping", pt.description)
            continue

        tx = Transaction(
            date=pt.date,
            amount=pt.amount,
            type=TransactionType(pt.tx_type),
            category_id=category_id,
            description=pt.description or None,
            payment_method="bank_transfer",
            source=source.value if source else "ocr",
            import_job_id=job.id,
            confidence_score=pt.confidence,
            needs_review=pt.confidence < REVIEW_THRESHOLD,
        )
        db.add(tx)
        committed += 1

    db.commit()
    return committed


def _is_duplicate(db: Session, pt: ParsedTransaction) -> bool:
    """Dedup on (date, amount, tx_type). Loose enough to catch re-uploads."""
    return db.query(Transaction).filter(
        Transaction.date == pt.date,
        Transaction.amount == pt.amount,
        Transaction.type == TransactionType(pt.tx_type),
    ).first() is not None


def _resolve_category(db: Session, pt: ParsedTransaction) -> Optional[int]:
    """
    Find the best matching category for a parsed transaction.
    Priority: category_hint name match → type-appropriate fallback.
    """
    tx_type = TransactionType(pt.tx_type)

    if pt.category_hint:
        cat = (
            db.query(Category)
            .filter(
                Category.name.ilike(f"%{pt.category_hint}%"),
                Category.type == tx_type,
                Category.is_active == True,
            )
            .first()
        )
        if cat:
            return cat.id

    # Fallback: first active category of the right type named "Others"
    cat = (
        db.query(Category)
        .filter(
            Category.name.ilike("%others%"),
            Category.type == tx_type,
            Category.is_active == True,
        )
        .first()
    )
    if cat:
        return cat.id

    # Last resort: any active category of the right type
    cat = (
        db.query(Category)
        .filter(Category.type == tx_type, Category.is_active == True)
        .first()
    )
    return cat.id if cat else None


def _done(db: Session, job: ImportJob, *, transaction_count: int) -> None:
    job.status = ImportJobStatus.DONE
    job.transaction_count = transaction_count
    job.processed_at = datetime.now(timezone.utc)
    db.commit()
    log.info("Job %d → DONE (%d transactions)", job.id, transaction_count)


def _fail(db: Session, job: ImportJob, reason: str) -> None:
    job.status = ImportJobStatus.FAILED
    job.error_message = reason
    job.processed_at = datetime.now(timezone.utc)
    db.commit()
    log.warning("Job %d → FAILED: %s", job.id, reason)
