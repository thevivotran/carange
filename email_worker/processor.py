"""Process one email from the ingest log: parse, commit transactions, notify."""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.database import EmailIngestLog
from app.services.ingest_service import IngestItem, commit_ingest_batch
from app.notify import telegram
from email_worker.email_parser import route_and_parse
from email_worker.parsers.base import ParsedEmailTransaction

log = logging.getLogger("email_worker.processor")


def process_email(log_row: EmailIngestLog, raw_message: bytes, db: Session) -> None:
    """Full pipeline for one email: parse → commit → notify."""
    from email_worker.email_parser import extract_email_parts

    try:
        message_id, sender, subject, body_text = extract_email_parts(raw_message)
    except Exception as exc:
        _fail(db, log_row, f"MIME parse error: {exc}")
        return

    # Populate log metadata if not already set
    if not log_row.sender:
        log_row.sender = sender
        log_row.subject = subject
        db.flush()

    body_html = ""  # extract_email_parts returns text; html handled internally
    parsed, parser_name = route_and_parse(sender, subject, body_text, body_html)

    if not parsed:
        log.info("Email %s: no transactions found (parser=%s)", log_row.message_id, parser_name)
        _done(db, log_row, count=0)
        return

    items = [_to_ingest_item(p) for p in parsed]
    committed = commit_ingest_batch(db, items, source_tag="email", email_ingest_log_id=log_row.id)
    _done(db, log_row, count=len(committed))

    # Telegram ping per transaction
    for tx in committed:
        try:
            db.refresh(tx)
            telegram.send_transaction_ping(tx)
        except Exception as exc:
            log.warning("Telegram ping failed for tx %d: %s", tx.id, exc)


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
    db.commit()
    log.info("Email %s → done (%d tx)", row.message_id, count)


def _fail(db: Session, row: EmailIngestLog, reason: str) -> None:
    row.status = "failed"
    row.error_message = reason
    row.processed_at = datetime.now(timezone.utc)
    db.commit()
    log.warning("Email %s → failed: %s", row.message_id, reason)
