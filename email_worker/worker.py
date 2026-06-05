"""Email worker — polls a Gmail IMAP inbox every POLL_INTERVAL seconds.

Required env vars:
  IMAP_HOST        — e.g. imap.gmail.com
  IMAP_USER        — full Gmail address
  IMAP_PASSWORD    — Gmail App Password (not account password; 2FA required)
  DATABASE_URL     — same SQLite path as the main app

Optional:
  POLL_INTERVAL      — seconds between polls (default 300)
  IMAP_FOLDER        — mailbox to watch (default INBOX)
  STUCK_TIMEOUT_MIN  — minutes before a pending row is reclaimed (default 30)
"""

import imaplib
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from app.models.database import EmailIngestLog, SessionLocal, DATABASE_URL
from app.models.database import engine
from sqlalchemy import event as sa_event

log = logging.getLogger("email_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
STUCK_TIMEOUT_MIN = int(os.getenv("STUCK_TIMEOUT_MIN", "30"))
LIVENESS_FILE = "/tmp/worker_alive"


# SQLite write-contention timeout — only runs on SQLite connections
@sa_event.listens_for(engine, "connect")
def _set_busy(dbapi_conn, _):
    if DATABASE_URL.startswith("sqlite"):
        dbapi_conn.execute("PRAGMA busy_timeout=5000")


def _touch_liveness():
    try:
        open(LIVENESS_FILE, "w").close()
    except OSError:
        pass


def _fetch_unread(imap: imaplib.IMAP4_SSL) -> list[tuple[bytes, bytes]]:
    """Return list of (uid, raw_message) for unseen emails."""
    imap.select(IMAP_FOLDER, readonly=False)
    _, data = imap.uid("search", None, "UNSEEN")
    uids = data[0].split() if data and data[0] else []
    results = []
    for uid in uids:
        _, msg_data = imap.uid("fetch", uid, "(RFC822)")
        if msg_data and msg_data[0]:
            raw = msg_data[0][1]
            results.append((uid, raw))
    return results


def _mark_seen(imap: imaplib.IMAP4_SSL, uid: bytes) -> None:
    imap.uid("store", uid, "+FLAGS", "\\Seen")


def _reclaim_stuck_pending(db) -> None:
    """Reset log rows stuck in 'pending' longer than STUCK_TIMEOUT_MIN.

    A pending row with no processed_at means processing never completed
    (e.g. worker was killed mid-run). Reset it so the email is reprocessed
    on the next UNSEEN poll rather than silently skipped.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_TIMEOUT_MIN)
    stuck = (
        db.query(EmailIngestLog)
        .filter(
            EmailIngestLog.status == "pending",
            EmailIngestLog.created_at < cutoff,
        )
        .all()
    )
    for row in stuck:
        log.warning(
            "Reclaiming stuck pending email log %d (message_id=%s, created=%s)",
            row.id,
            row.message_id,
            row.created_at,
        )
        db.delete(row)
    if stuck:
        db.commit()


def _already_processed(db, message_id: str) -> bool:
    """Return True for done or failed emails; False for pending (interrupted) or absent.

    - done: successfully processed — skip and mark SEEN
    - failed: permanently failed — skip and mark SEEN (no point retrying a parse error)
    - pending: processing was interrupted (worker killed mid-run); reclaimed by
      _reclaim_stuck_pending so the email is reprocessed on the next poll
    """
    row = db.query(EmailIngestLog).filter(EmailIngestLog.message_id == message_id).first()
    return row is not None and row.status in ("done", "failed")


def _create_log_row(db, message_id: str) -> EmailIngestLog:
    row = EmailIngestLog(
        message_id=message_id,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def poll_once() -> None:
    if not IMAP_USER or not IMAP_PASSWORD:
        log.error("IMAP_USER or IMAP_PASSWORD not set — skipping poll")
        return

    # Reclaim any log rows stuck in pending from a previous crashed run so they
    # are not silently skipped when the same UNSEEN email is encountered again.
    with SessionLocal() as db:
        _reclaim_stuck_pending(db)

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(IMAP_USER, IMAP_PASSWORD)
    except Exception as exc:
        log.error("IMAP login failed: %s", exc)
        return

    try:
        emails = _fetch_unread(imap)
        log.info("Found %d unread emails", len(emails))

        for uid, raw in emails:
            db = SessionLocal()
            log_row = None
            try:
                from email_worker.email_parser import extract_email_parts
                from email_worker.processor import process_email

                message_id, sender, subject, *_ = extract_email_parts(raw)

                if not message_id:
                    # Fabricate a unique ID from uid when header is missing
                    message_id = f"uid-{uid.decode()}-{int(time.time())}"

                if _already_processed(db, message_id):
                    log.debug("Already processed: %s", message_id)
                    _mark_seen(imap, uid)
                    continue

                log.info("Processing email: %s / %s", sender, subject)
                log_row = _create_log_row(db, message_id)
                log_row.sender = sender
                log_row.subject = subject
                db.commit()

                process_email(log_row, raw, db)
                _mark_seen(imap, uid)

            except Exception as exc:
                log.exception("Unhandled error processing email uid=%s: %s", uid, exc)
                # Mark the log row failed so it doesn't stay pending and get
                # silently skipped on the next restart.
                if log_row is not None:
                    try:
                        log_row.status = "failed"
                        log_row.error_message = f"Internal error: {exc}"
                        log_row.processed_at = datetime.now(timezone.utc)
                        db.commit()
                    except Exception:
                        log.exception("Could not mark log row %d as failed", log_row.id)
            finally:
                db.close()

    finally:
        try:
            imap.logout()
        except Exception:
            pass


def run() -> None:
    log.info("Email worker started — polling %s as %s every %ds", IMAP_HOST, IMAP_USER, POLL_INTERVAL)
    while True:
        _touch_liveness()
        try:
            poll_once()
        except Exception as exc:
            log.exception("poll_once raised: %s", exc)
        _touch_liveness()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
