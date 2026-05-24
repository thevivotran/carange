"""
Job processor — drives the full OCR pipeline for one ImportJob.

Extraction priority:
  1. Ollama vision (Qwen3.5-9B) — if OLLAMA_URL is set; handles any screenshot source
  2. PaddleOCR + source-specific parser — fallback when Ollama is unavailable
"""

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database import Category, ImportJob, ImportJobStatus, ImportSource, Transaction, TransactionType
from app.services import ollama as _ollama
from ocr_worker import ocr as _ocr_mod  # module ref — allows monkeypatching in tests
from ocr_worker.parsers import get_parser
from ocr_worker.parsers.base import normalize_vi
from ocr_worker.source_detector import detect_source
from ocr_worker.types import ParsedTransaction

log = logging.getLogger("ocr_worker.processor")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
REVIEW_THRESHOLD = float(os.getenv("REVIEW_THRESHOLD", "0.95"))
ANOMALY_MULTIPLIER = float(os.getenv("ANOMALY_MULTIPLIER", "3.0"))
ANOMALY_MIN_SAMPLES = int(os.getenv("ANOMALY_MIN_SAMPLES", "3"))
VISION_CONFIDENCE = 0.85  # baseline confidence assigned to vision-extracted transactions


def _resolve_file_path(stored: str) -> str:
    """Resolve job.file_path to an absolute path across all storage formats."""
    if os.path.isabs(stored):
        return stored
    return os.path.join(UPLOAD_DIR, os.path.basename(stored))


def process_job(job: ImportJob, db: Session) -> None:
    log.info("Processing job %d: %s", job.id, job.filename)

    file_path = _resolve_file_path(job.file_path)
    if not os.path.isfile(file_path):
        _fail(db, job, f"Image not found on disk: {file_path}")
        return

    # ── Path 1: Ollama vision extraction ─────────────────────────────────────
    if _ollama.is_enabled():
        parsed = _extract_via_vision(file_path)
        if parsed is not None:
            log.info("Job %d: vision extracted %d candidates", job.id, len(parsed))
            job.detected_source = None  # vision handles any source
            db.flush()
            committed = _commit_transactions(db, job, parsed, source=None)
            _done(db, job, transaction_count=committed)
            return
        log.info("Job %d: vision extraction empty or failed — falling back to PaddleOCR", job.id)

    # ── Path 2: PaddleOCR + source-specific parser (fallback) ────────────────
    try:
        blocks = _ocr_mod.extract_blocks(file_path)
    except Exception as exc:
        _fail(db, job, f"OCR failed: {exc}")
        return

    effective_source = job.source_hint or detect_source(blocks)
    if effective_source != job.detected_source:
        job.detected_source = effective_source
        db.flush()

    log.info("Job %d: detected source = %s", job.id, effective_source)

    if not blocks:
        log.info("Job %d: no text detected — marking done (0 tx)", job.id)
        _done(db, job, transaction_count=0)
        return

    parser = get_parser(effective_source)
    try:
        parsed = parser.parse(blocks)
    except Exception as exc:
        _fail(db, job, f"Parser error: {exc}")
        return

    log.info("Job %d: parser found %d candidate transactions", job.id, len(parsed))
    committed = _commit_transactions(db, job, parsed, effective_source)
    _done(db, job, transaction_count=committed)


# ── Vision extraction ─────────────────────────────────────────────────────────


def _extract_via_vision(file_path: str) -> Optional[List[ParsedTransaction]]:
    """Send the image to Ollama vision and parse the JSON response.

    Returns a list of ParsedTransaction on success, None if Ollama is
    unavailable, returns an empty result, or produces unparseable output.
    """
    today_str = date.today().strftime("%Y-%m-%d")
    prompt = (
        f"Today's date: {today_str}\n\n"
        "Extract all financial transactions visible in this screenshot.\n"
        "Return a JSON array. Each element must have exactly these fields:\n"
        '  "date": "YYYY-MM-DD"\n'
        '  "amount": positive number in VND\n'
        '  "type": "expense" or "income"\n'
        '  "description": merchant or transaction description (keep original language)\n'
        '  "category_hint": one of: Food & Dining, Transportation, Shopping, '
        "Entertainment, Utilities, Healthcare, Education, Housing, Insurance, "
        "Salary, Bonus, Investment, Others\n\n"
        "If no transactions are found return []. Return ONLY the JSON array, no explanation."
    )
    raw = _ollama.vision_sync(
        file_path,
        prompt=prompt,
        system=(
            "You extract structured financial data from payment app screenshots. "
            "Always return valid JSON. Never add explanatory text outside the JSON array."
        ),
    )
    if not raw:
        return None

    # Extract the JSON array from the response (model may wrap it in markdown)
    match = re.search(r"\[.*?\]", raw, re.DOTALL)
    if not match:
        log.debug("Vision response contained no JSON array: %s", raw[:200])
        return None

    try:
        items = json.loads(match.group())
    except json.JSONDecodeError as exc:
        log.debug("Vision JSON parse error: %s", exc)
        return None

    parsed: List[ParsedTransaction] = []
    for item in items:
        try:
            parsed.append(
                ParsedTransaction(
                    date=date.fromisoformat(item["date"]),
                    amount=float(item["amount"]),
                    tx_type=str(item["type"]).lower(),
                    description=str(item.get("description", "")),
                    confidence=VISION_CONFIDENCE,
                    category_hint=item.get("category_hint"),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("Skipping malformed vision item %s: %s", item, exc)

    return parsed if parsed else None


# ── Commit ────────────────────────────────────────────────────────────────────


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

        anomaly = _is_anomaly(db, pt, category_id)
        if anomaly:
            log.info(
                "Anomaly: '%s' %.0f VND is %.1fx+ the 90-day avg for this category",
                pt.description,
                pt.amount,
                ANOMALY_MULTIPLIER,
            )

        tx = Transaction(
            date=pt.date,
            amount=pt.amount,
            type=TransactionType(pt.tx_type),
            category_id=category_id,
            description=normalize_vi(pt.description) if pt.description else None,
            payment_method="bank_transfer",
            source=source.value if source else "ocr",
            import_job_id=job.id,
            confidence_score=pt.confidence,
            needs_review=pt.confidence < REVIEW_THRESHOLD or anomaly,
        )
        db.add(tx)
        committed += 1

    db.commit()
    return committed


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_duplicate(db: Session, pt: ParsedTransaction) -> bool:
    """Dedup on (date, amount, tx_type). Loose enough to catch re-uploads."""
    return (
        db.query(Transaction)
        .filter(
            Transaction.date == pt.date,
            Transaction.amount == pt.amount,
            Transaction.type == TransactionType(pt.tx_type),
        )
        .first()
        is not None
    )


def _is_anomaly(db: Session, pt: ParsedTransaction, category_id: int) -> bool:
    """Return True if pt.amount is ANOMALY_MULTIPLIER× the 90-day category average.

    Requires at least ANOMALY_MIN_SAMPLES prior transactions to establish a baseline,
    so new categories are never flagged.
    """
    cutoff = date.today() - timedelta(days=90)
    row = (
        db.query(
            func.count(Transaction.id).label("n"),
            func.avg(Transaction.amount).label("avg"),
        )
        .filter(
            Transaction.category_id == category_id,
            Transaction.type == TransactionType(pt.tx_type),
            Transaction.date >= cutoff,
            Transaction.deleted_at.is_(None),
        )
        .first()
    )
    if not row or row.n < ANOMALY_MIN_SAMPLES or not row.avg:
        return False
    return pt.amount > row.avg * ANOMALY_MULTIPLIER


def _resolve_category(db: Session, pt: ParsedTransaction) -> Optional[int]:
    """
    Find the best matching category for a parsed transaction.
    Priority: category_hint name match → LLM inference (if Ollama available) → keyword fallback.
    """
    tx_type = TransactionType(pt.tx_type)
    active_cats = db.query(Category).filter(Category.type == tx_type, Category.is_active == True).all()

    if not active_cats:
        return None

    if pt.category_hint:
        for cat in active_cats:
            if pt.category_hint.lower() in cat.name.lower():
                return cat.id

    # LLM inference — only fires when OLLAMA_URL is set, falls back silently
    if _ollama.is_enabled() and pt.description:
        cat_names = ", ".join(c.name for c in active_cats)
        llm_result = _ollama.generate_sync(
            prompt=(
                f"Transaction type: {pt.tx_type}\n"
                f"Description: {pt.description}\n"
                f"Amount: {pt.amount:.0f} VND\n\n"
                f"Available categories: {cat_names}\n\n"
                "Reply with ONLY the category name that best fits. No explanation."
            ),
            system=(
                "You are a Vietnamese personal finance assistant. "
                "Categorize transactions accurately based on their description. "
                "Always reply with exactly one category name from the provided list."
            ),
        )
        if llm_result:
            for cat in active_cats:
                if cat.name.lower() == llm_result.lower():
                    log.debug("LLM categorized '%s' → '%s'", pt.description, cat.name)
                    return cat.id
            for cat in active_cats:
                if cat.name.lower() in llm_result.lower():
                    log.debug("LLM partial match '%s' → '%s'", pt.description, cat.name)
                    return cat.id
            log.debug("LLM returned unknown category '%s' for '%s'", llm_result, pt.description)

    # Keyword fallback
    for cat in active_cats:
        if "khác" in cat.name.lower() or "others" in cat.name.lower():
            return cat.id

    return active_cats[0].id


def _cleanup_file(job: ImportJob) -> None:
    if not job.file_path:
        return
    full_path = _resolve_file_path(job.file_path)
    if os.path.isfile(full_path):
        try:
            os.remove(full_path)
            log.info("Job %d: deleted image %s", job.id, full_path)
        except OSError as exc:
            log.warning("Job %d: could not delete image %s: %s", job.id, full_path, exc)


def _done(db: Session, job: ImportJob, *, transaction_count: int) -> None:
    job.status = ImportJobStatus.DONE
    job.transaction_count = transaction_count
    job.processed_at = datetime.now(timezone.utc)
    db.commit()
    _cleanup_file(job)
    log.info("Job %d → DONE (%d transactions)", job.id, transaction_count)


def _fail(db: Session, job: ImportJob, reason: str) -> None:
    job.status = ImportJobStatus.FAILED
    job.error_message = reason
    job.processed_at = datetime.now(timezone.utc)
    db.commit()
    _cleanup_file(job)
    log.warning("Job %d → FAILED: %s", job.id, reason)
