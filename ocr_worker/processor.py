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
from datetime import date, datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.database import ImportJob, ImportJobStatus, ImportSource
from app.services import ollama as _ollama
from app.services.ingest_service import IngestItem, commit_ingest_batch
from ocr_worker import ocr as _ocr_mod  # module ref — allows monkeypatching in tests
from ocr_worker.parsers import get_parser
from ocr_worker.parsers.base import normalize_vi
from ocr_worker.source_detector import detect_source
from ocr_worker.types import ParsedTransaction

log = logging.getLogger("ocr_worker.processor")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
VISION_CONFIDENCE = 0.85
LOW_CONF_THRESHOLD = float(os.getenv("LOW_CONF_THRESHOLD", "0.75"))


class TransientJobError(Exception):
    """A failure worth retrying (e.g. OCR engine crash). The worker reschedules
    the job with backoff instead of failing it permanently, and the uploaded
    image is kept on disk so the retry has something to process."""


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
    # None = extraction failed (fall back to PaddleOCR); [] = the model explicitly
    # found no transactions (trust it — the generic OCR parser would only
    # hallucinate transactions out of stray numbers).
    if _ollama.is_enabled():
        items = _extract_via_vision(file_path)
        if items is not None:
            log.info("Job %d: vision extracted %d candidates", job.id, len(items))
            job.detected_source = None
            db.flush()
            committed = commit_ingest_batch(db, items, source_tag="ocr", import_job_id=job.id)
            _done(db, job, transaction_count=len(committed))
            return
        log.info("Job %d: vision extraction failed — falling back to PaddleOCR", job.id)

    # ── Path 2: PaddleOCR + source-specific parser (fallback) ────────────────
    try:
        blocks = _ocr_mod.extract_blocks(file_path)
    except Exception as exc:
        raise TransientJobError(f"OCR failed: {exc}") from exc

    full_text = " ".join(b.text.lower() for b in blocks)

    learned_txns = _try_learned_parser(db, full_text, blocks, job.id)
    if learned_txns is not None:
        items = _parsed_to_ingest_items(learned_txns, None)
        committed = commit_ingest_batch(db, items, source_tag="ocr", import_job_id=job.id)
        _done(db, job, transaction_count=len(committed))
        return

    effective_source = job.source_hint or detect_source(blocks)
    if effective_source != job.detected_source:
        job.detected_source = effective_source
        db.flush()

    log.info("Job %d: detected source = %s", job.id, effective_source)

    if effective_source is None and _ollama.is_enabled():
        generated = _generate_and_save_parser(db, file_path, blocks, job.id)
        if generated is not None:
            committed = commit_ingest_batch(db, generated, source_tag="ocr", import_job_id=job.id)
            _done(db, job, transaction_count=len(committed))
            return

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

    if _ollama.is_enabled() and parsed and any(t.confidence < LOW_CONF_THRESHOLD for t in parsed):
        log.info("Job %d: low confidence — triggering vision fallback", job.id)
        vision_items = _extract_via_vision(file_path)
        if vision_items is not None:
            log.info("Job %d: vision fallback returned %d items", job.id, len(vision_items))
            committed = commit_ingest_batch(db, vision_items, source_tag="ocr", import_job_id=job.id)
            _done(db, job, transaction_count=len(committed))
            return
        log.info("Job %d: vision fallback failed — using original parse", job.id)

    source_tag = effective_source.value if effective_source else "ocr"
    items = _parsed_to_ingest_items(parsed, effective_source)
    committed = commit_ingest_batch(db, items, source_tag=source_tag, import_job_id=job.id)
    _done(db, job, transaction_count=len(committed))


# ── Vision extraction ─────────────────────────────────────────────────────────


def _try_learned_parser(db, full_text, blocks, job_id):
    from ocr_worker.learned_parser_store import lookup, run_parser

    lp = lookup(db, full_text)
    if lp is None:
        return None
    log.info("Job %d: matched learned parser %r", job_id, lp.source_name)
    return run_parser(lp.extraction_script, blocks)


def _generate_and_save_parser(db, file_path, blocks, job_id):
    try:
        from ocr_worker.learned_parser_store import run_parser, save

        today_str = date.today().strftime("%Y-%m-%d")
        system = (
            "You are a financial data extraction and parser generation assistant. "
            "Return only valid JSON, no explanation."
        )
        prompt = (
            f"Today: {today_str}\n"
            "Extract all financial transactions from this screenshot AND generate "
            "a reusable Python parser function for this layout.\n"
            "Return a single JSON object with exactly two keys:\n"
            '  "transactions": [...same schema as standard extraction...]\n'
            '  "parser": {\n'
            '    "source_name": "<snake_case id>",\n'
            '    "detection_keywords": ["<keyword1>", ...],\n'
            '    "extraction_script": "def parse(blocks):\\n    ..."\n'
            "  }\n"
            "If no transactions, set transactions to [].\n"
            "The extraction_script must be a complete Python function named parse "
            "that accepts list[TextBlock] and returns list[ParsedTransaction].\n"
            "Import nothing — use only: re, math, date, datetime, TextBlock, ParsedTransaction."
        )
        raw = _ollama.vision_sync(file_path, prompt=prompt, system=system)
        if not raw:
            return None

        text = _FENCE_RE.sub("", raw.strip()).strip()
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return None

        raw_transactions = obj.get("transactions")
        parser_dict = obj.get("parser")

        result_items = None
        if isinstance(raw_transactions, list) and raw_transactions:
            result_items = []
            for item in raw_transactions:
                try:
                    tx_type = str(item["type"]).strip().lower()
                    if tx_type not in ("expense", "income"):
                        raise ValueError(f"unknown type {tx_type!r}")
                    result_items.append(
                        IngestItem(
                            date=date.fromisoformat(item["date"]),
                            amount=_coerce_amount(item["amount"]),
                            tx_type=tx_type,
                            description=str(item.get("description", "")),
                            confidence=VISION_CONFIDENCE,
                            category_hint=item.get("category_hint"),
                        )
                    )
                except (KeyError, ValueError, TypeError) as exc:
                    log.debug("Skipping malformed generated item %s: %s", item, exc)

        if not isinstance(parser_dict, dict):
            return result_items

        source_name = parser_dict.get("source_name")
        keywords = parser_dict.get("detection_keywords")
        script = parser_dict.get("extraction_script")

        if not source_name or not isinstance(keywords, list) or not script:
            return result_items

        if "def parse(" not in script:
            return result_items

        dry_run = run_parser(script, blocks[:3])
        if dry_run is None and blocks[:3]:
            return result_items

        save(db, source_name, keywords, script)
        log.info("Job %d: saved learned parser %r", job_id, source_name)

        return result_items
    except Exception as exc:
        log.warning("Job %d: _generate_and_save_parser failed: %s", job_id, exc)
        return None


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# VND-formatted string amount: dots/commas are thousands separators ("45.000")
_VN_GROUPED_AMOUNT_RE = re.compile(r"-?\d{1,3}(?:[.,]\d{3})+")


def _parse_json_array(raw: str) -> Optional[list]:
    """Extract a JSON array from a model response, tolerating markdown fences,
    surrounding prose, and `]` characters inside string values."""
    text = _FENCE_RE.sub("", raw.strip()).strip()

    candidates = []
    if text.startswith("["):
        candidates.append(text)
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    m = re.search(r"\[.*?\]", text, re.DOTALL)
    if m:
        candidates.append(m.group())

    for cand in candidates:
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    return None


def _coerce_amount(value) -> float:
    """Accept numeric amounts and VND-formatted strings ('45.000đ' → 45000).
    float('45.000') would silently give 45.0 — a 1000× error."""
    if isinstance(value, bool):
        raise ValueError(f"bad amount {value!r}")
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        cleaned = re.sub(r"[^\d.,\-]", "", str(value))
        if _VN_GROUPED_AMOUNT_RE.fullmatch(cleaned):
            cleaned = cleaned.replace(".", "").replace(",", "")
        else:
            cleaned = cleaned.replace(",", "")
        amount = float(cleaned)
    if amount <= 0:
        raise ValueError(f"non-positive amount {value!r}")
    return amount


def _extract_via_vision(file_path: str) -> Optional[List[IngestItem]]:
    """Returns the extracted items, [] when the model explicitly reported no
    transactions, or None when extraction failed (caller falls back to OCR)."""
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

    raw_items = _parse_json_array(raw)
    if raw_items is None:
        log.debug("Vision response contained no parseable JSON array: %s", raw[:200])
        return None

    result: List[IngestItem] = []
    for item in raw_items:
        try:
            tx_type = str(item["type"]).strip().lower()
            if tx_type not in ("expense", "income"):
                raise ValueError(f"unknown type {tx_type!r}")
            result.append(
                IngestItem(
                    date=date.fromisoformat(item["date"]),
                    amount=_coerce_amount(item["amount"]),
                    tx_type=tx_type,
                    description=str(item.get("description", "")),
                    confidence=VISION_CONFIDENCE,
                    category_hint=item.get("category_hint"),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("Skipping malformed vision item %s: %s", item, exc)

    # Every item malformed → extraction failed; a genuinely empty array is a
    # valid "no transactions in this screenshot" answer.
    if raw_items and not result:
        return None
    return result


def _parsed_to_ingest_items(parsed: List[ParsedTransaction], source: Optional[ImportSource]) -> List[IngestItem]:
    """Convert OCR ParsedTransaction list to generic IngestItem list."""
    items = []
    for pt in parsed:
        items.append(
            IngestItem(
                date=pt.date,
                amount=pt.amount,
                tx_type=pt.tx_type,
                description=normalize_vi(pt.description) if pt.description else None,
                confidence=pt.confidence,
                category_hint=pt.category_hint,
                payment_method="bank_transfer",
            )
        )
    return items


# ── Back-compat constants + shims (used by existing test suite) ──────────────

from app.services.ingest_service import ANOMALY_MULTIPLIER, ANOMALY_MIN_SAMPLES, REVIEW_THRESHOLD  # noqa: F401


# ── Back-compat shims (used by existing test suite) ──────────────────────────


def _is_anomaly(db: Session, pt: "ParsedTransaction", category_id: int) -> bool:
    """Shim: delegate to ingest_service._is_anomaly using ParsedTransaction."""
    from app.services.ingest_service import IngestItem
    from app.services.ingest_service import _is_anomaly as _svc_anomaly

    item = IngestItem(
        date=pt.date,
        amount=pt.amount,
        tx_type=pt.tx_type,
        description=pt.description,
        confidence=pt.confidence,
    )
    return _svc_anomaly(db, item, category_id)


# ── Job state helpers ─────────────────────────────────────────────────────────


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
