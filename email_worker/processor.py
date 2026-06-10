"""Process one email from the ingest log: parse, commit transactions, notify.

LLMUnavailableError raised by the generic fallback parser propagates to the
caller — the worker schedules a retry instead of marking the email done.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.database import EmailIngestLog
from app.services.ingest_service import IngestItem, commit_ingest_batch
from email_worker.email_parser import route_and_parse
from email_worker.parsers.base import ParsedEmailTransaction

log = logging.getLogger("email_worker.processor")


def process_email(log_row: EmailIngestLog, raw_message: bytes, db: Session) -> None:
    """Full pipeline for one email: parse → commit → notify."""
    from email_worker.email_parser import extract_email_parts

    try:
        message_id, sender, subject, body_text, body_html = extract_email_parts(raw_message)
    except Exception as exc:
        _fail(db, log_row, f"MIME parse error: {exc}")
        return

    # Populate log metadata if not already set
    if not log_row.sender:
        log_row.sender = sender
        log_row.subject = subject
        db.flush()

    parsed, parser_name = route_and_parse(sender, subject, body_text, body_html)
    log_row.parser_name = parser_name

    if not parsed:
        log.info("Email %s: no transactions found (parser=%s)", log_row.message_id, parser_name)
        _done(db, log_row, count=0)
        return

    items = [_to_ingest_item(p) for p in parsed]
    committed = commit_ingest_batch(db, items, source_tag="email", email_ingest_log_id=log_row.id)
    _done(db, log_row, count=len(committed))


def _to_ingest_item(p: ParsedEmailTransaction) -> IngestItem:
    return IngestItem(
        date=p.date,
        amount=p.amount,
        tx_type=p.tx_type,
        description=p.description,
        confidence=p.confidence,
        category_hint=p.category_hint,
        payment_method=p.payment_method,
    )


def _done(db: Session, row: EmailIngestLog, count: int) -> None:
    row.status = "done"
    row.transaction_count = count
    row.processed_at = datetime.now(timezone.utc)
    if count > 0:
        # Transactions committed — the raw copy has served its purpose.
        # Zero-transaction rows keep it so they can be replayed after a parser fix.
        row.raw_email = None
        row.raw_size = None
    db.commit()
    log.info("Email %s → done (%d tx)", row.message_id, count)


def _fail(db: Session, row: EmailIngestLog, reason: str) -> None:
    row.status = "failed"
    row.error_message = reason
    row.processed_at = datetime.now(timezone.utc)
    db.commit()
    log.warning("Email %s → failed: %s", row.message_id, reason)
