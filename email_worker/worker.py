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
MAX_EMAIL_RETRIES = int(os.getenv("MAX_EMAIL_RETRIES", "3"))
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
    """Delete log rows that are stuck in 'pending' from a crashed run.

    Only targets rows where retry_after IS NULL — those represent a worker
    crash mid-processing, not a scheduled retry. Deleting them lets the UNSEEN
    email be picked up again on the next poll.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_TIMEOUT_MIN)
    stuck = (
        db.query(EmailIngestLog)
        .filter(
            EmailIngestLog.status == "pending",
            EmailIngestLog.retry_after.is_(None),
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


def _check_email_status(db, message_id: str) -> tuple[bool, bool]:
    """Return (skip_this_poll, mark_seen_in_imap) for the given message_id.

    Status semantics:
    - absent:                        → process it (False, False)
    - done:                          → skip + mark SEEN (True, True)
    - failed at max retries:         → skip + mark SEEN (True, True)
    - pending + retry_after future:  → skip, keep UNSEEN so it retries (True, False)
    - pending + retry due / no backoff: → reprocess (False, False)
    """
    row = db.query(EmailIngestLog).filter(EmailIngestLog.message_id == message_id).first()
    if row is None:
        return False, False
    if row.status == "done":
        return True, True
    if row.status == "failed" and (row.retry_count or 0) >= MAX_EMAIL_RETRIES:
        return True, True
    if row.status == "pending" and row.retry_after is not None:
        due = row.retry_after
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > datetime.now(timezone.utc):
            return True, False  # retry not yet due — leave UNSEEN in IMAP
    return False, False


def _get_or_create_log_row(db, message_id: str) -> EmailIngestLog:
    """Return existing log row (retry scenario) or create a fresh one."""
    row = db.query(EmailIngestLog).filter(EmailIngestLog.message_id == message_id).first()
    if row is not None:
        # Clear retry_after so the row is treated as in-progress
        row.retry_after = None
        db.commit()
        return row
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

                skip, do_mark_seen = _check_email_status(db, message_id)
                if skip:
                    log.debug("Skipping %s (mark_seen=%s)", message_id, do_mark_seen)
                    if do_mark_seen:
                        _mark_seen(imap, uid)
                    continue

                log.info("Processing email: %s / %s", sender, subject)
                log_row = _get_or_create_log_row(db, message_id)
                log_row.sender = sender
                log_row.subject = subject
                db.commit()

                process_email(log_row, raw, db)
                _mark_seen(imap, uid)

            except Exception as exc:
                log.exception("Unhandled error processing email uid=%s: %s", uid, exc)
                if log_row is not None:
                    try:
                        retry_count = (log_row.retry_count or 0) + 1
                        if retry_count <= MAX_EMAIL_RETRIES:
                            # Leave UNSEEN in IMAP — it will be retried next poll when due.
                            backoff_secs = (2 ** (retry_count - 1)) * 60
                            log_row.status = "pending"
                            log_row.retry_count = retry_count
                            log_row.retry_after = datetime.now(timezone.utc) + timedelta(seconds=backoff_secs)
                            log_row.error_message = f"Retry {retry_count}/{MAX_EMAIL_RETRIES}: {exc}"
                            db.commit()
                            log.warning(
                                "Email %s → retry %d/%d in %ds",
                                message_id,
                                retry_count,
                                MAX_EMAIL_RETRIES,
                                backoff_secs,
                            )
                            continue  # skip _mark_seen — email stays UNSEEN for retry
                        else:
                            log_row.status = "failed"
                            log_row.error_message = f"Max retries exceeded. Last: {exc}"
                            log_row.processed_at = datetime.now(timezone.utc)
                            db.commit()
                    except Exception:
                        log.exception("Could not update log row %d after failure", log_row.id)
                _mark_seen(imap, uid)
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
