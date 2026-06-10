"""Email worker — IMAP ingestion with UID-cursor tracking, IDLE push, DB-driven retries.

How ingestion works:
  • New messages are discovered by UID, not by the \\Seen flag: the worker keeps a
    per-(account, folder) high-water mark in imap_folder_state and searches
    UID <last+1>:* each cycle. Reading the mailbox from another client can no
    longer starve the worker. The cursor resets when UIDVALIDITY changes.
  • Bodies are fetched with BODY.PEEK[] (never sets \\Seen implicitly) and stored
    zlib-compressed on the log row, so retries and manual reprocessing replay from
    the database — the IMAP message is never needed twice.
  • When the server supports IDLE, new mail is processed within seconds; otherwise
    the worker falls back to sleeping POLL_INTERVAL between cycles.

Required env vars (DB settings take precedence, reloaded every cycle):
  IMAP_HOST        — e.g. imap.gmail.com
  IMAP_USER        — full Gmail address
  IMAP_PASSWORD    — Gmail App Password (not account password; 2FA required)
  DATABASE_URL     — same database as the main app

Optional:
  POLL_INTERVAL      — seconds between polls when IDLE is unavailable (default 300)
  IMAP_FOLDER        — mailbox to watch (default INBOX)
  IMAP_TIMEOUT       — socket timeout in seconds (default 60)
  STUCK_TIMEOUT_MIN  — minutes before a crashed 'pending' row is reclaimed (default 30)
  MAX_EMAIL_RETRIES  — retry attempts with exponential backoff (default 3)
  LLM_RETRY_MIN      — minutes between retries while the LLM is unreachable (default 30;
                       these retries do not count toward MAX_EMAIL_RETRIES)
"""

import email
import email.utils
import logging
import os
import time
import zlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from imapclient import IMAPClient, SEEN

from app.models.database import DATABASE_URL, EmailIngestLog, ImapFolderState, SessionLocal
from app.models.database import engine
from app.services.settings_service import get_setting, set_setting
from sqlalchemy import event as sa_event

log = logging.getLogger("email_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")
IMAP_FOLDER = os.getenv("IMAP_FOLDER", "INBOX")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
IMAP_TIMEOUT = int(os.getenv("IMAP_TIMEOUT", "60"))
STUCK_TIMEOUT_MIN = int(os.getenv("STUCK_TIMEOUT_MIN", "30"))
MAX_EMAIL_RETRIES = int(os.getenv("MAX_EMAIL_RETRIES", "3"))
LLM_RETRY_MIN = int(os.getenv("LLM_RETRY_MIN", "30"))

# Cadence of the IDLE wake-up: bounds liveness touches, retry latency, and how
# often config changes are picked up. IDLE itself wakes the loop early on new mail.
IDLE_CHECK_SECS = 60

# Reconnect backoff after consecutive session failures (bad password, network…)
RECONNECT_BACKOFF_BASE = 15
RECONNECT_BACKOFF_MAX = 1800

LIVENESS_FILE = "/tmp/worker_alive"


def _load_config() -> None:
    """Reload IMAP config from DB, falling back to env vars for each key."""
    global IMAP_HOST, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER, POLL_INTERVAL, STUCK_TIMEOUT_MIN, MAX_EMAIL_RETRIES
    try:
        with SessionLocal() as db:
            IMAP_HOST = get_setting(db, "imap_host") or os.getenv("IMAP_HOST", "imap.gmail.com")
            IMAP_USER = get_setting(db, "imap_user") or os.getenv("IMAP_USER", "")
            IMAP_PASSWORD = get_setting(db, "imap_password") or os.getenv("IMAP_PASSWORD", "")
            IMAP_FOLDER = get_setting(db, "imap_folder") or os.getenv("IMAP_FOLDER", "INBOX")
            interval_str = get_setting(db, "email_poll_interval") or os.getenv("POLL_INTERVAL", "300")
            POLL_INTERVAL = int(interval_str)
            stuck_str = get_setting(db, "stuck_timeout_min") or os.getenv("STUCK_TIMEOUT_MIN", "30")
            STUCK_TIMEOUT_MIN = int(stuck_str)
            retries_str = get_setting(db, "max_retries") or os.getenv("MAX_EMAIL_RETRIES", "3")
            MAX_EMAIL_RETRIES = int(retries_str)
    except Exception as exc:
        log.warning("Could not load config from DB, using env vars: %s", exc)


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


def _record_status(key: str, value: str) -> None:
    """Persist worker health for the Email Receipts panel (best-effort)."""
    try:
        with SessionLocal() as db:
            set_setting(db, key, value)
    except Exception as exc:
        log.debug("Could not record status %s: %s", key, exc)


# ── Raw message storage ───────────────────────────────────────────────────────


def _compress(raw: bytes) -> bytes:
    return zlib.compress(raw, 6)


def _decompress(blob: bytes) -> bytes:
    try:
        return zlib.decompress(blob)
    except zlib.error:
        return blob  # stored uncompressed (defensive)


# ── Header helpers ────────────────────────────────────────────────────────────


def _header_message_id(fetch_item: dict) -> str:
    """Extract Message-ID from a BODY.PEEK[HEADER.FIELDS …] fetch response."""
    for key, value in fetch_item.items():
        if isinstance(key, bytes) and b"HEADER.FIELDS" in key and isinstance(value, bytes):
            hdr = email.message_from_bytes(value)
            return (hdr.get("Message-ID") or hdr.get("Message-Id") or "").strip()
    return ""


def _body_from_fetch(fetch_item: dict) -> Optional[bytes]:
    """Extract the raw RFC 2822 message from a BODY.PEEK[] fetch response."""
    for key, value in fetch_item.items():
        if isinstance(key, bytes) and key.startswith(b"BODY[") and isinstance(value, bytes):
            return value
    return None


def _parse_received_at(raw: bytes) -> Optional[datetime]:
    try:
        date_hdr = email.message_from_bytes(raw).get("Date")
        return email.utils.parsedate_to_datetime(date_hdr) if date_hdr else None
    except Exception:
        return None


# ── Log row lifecycle ─────────────────────────────────────────────────────────


def _create_log_row(db, message_id: str, raw: bytes) -> EmailIngestLog:
    """Create a pending log row with the compressed raw message attached."""
    msg = email.message_from_bytes(raw)
    compressed = _compress(raw)
    row = EmailIngestLog(
        message_id=message_id,
        sender=(msg.get("From") or "").strip()[:200],
        subject=(msg.get("Subject") or "").strip()[:500],
        received_at=_parse_received_at(raw),
        status="pending",
        created_at=datetime.now(timezone.utc),
        raw_email=compressed,
        raw_size=len(compressed),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _process_row(db, row: EmailIngestLog, raw: bytes) -> None:
    """Run the processing pipeline for one row, handling retry bookkeeping.

    Failure semantics:
      • LLMUnavailableError → stay pending, retry in LLM_RETRY_MIN minutes without
        consuming a retry attempt (the GPU node may simply be powered off).
      • Any other exception → exponential backoff (1, 2, 4 min …); after
        MAX_EMAIL_RETRIES the row is marked failed (raw copy kept for replay).
    """
    from email_worker.parsers.base import LLMUnavailableError
    from email_worker.processor import process_email

    row.status = "pending"
    row.retry_after = None  # in-progress marker: stuck-row reclaim keys off this
    db.commit()

    now = datetime.now(timezone.utc)
    try:
        process_email(row, raw, db)
    except LLMUnavailableError as exc:
        db.rollback()
        row.status = "pending"
        row.retry_after = now + timedelta(minutes=LLM_RETRY_MIN)
        row.error_message = f"LLM unavailable — retrying every {LLM_RETRY_MIN} min: {exc}"
        db.commit()
        log.warning("Email %s → LLM unavailable, retry at %s", row.message_id, row.retry_after)
    except Exception as exc:
        db.rollback()
        log.exception("Error processing email %s: %s", row.message_id, exc)
        retry_count = (row.retry_count or 0) + 1
        row.retry_count = retry_count
        if retry_count <= MAX_EMAIL_RETRIES:
            backoff_secs = (2 ** (retry_count - 1)) * 60
            row.status = "pending"
            row.retry_after = now + timedelta(seconds=backoff_secs)
            row.error_message = f"Retry {retry_count}/{MAX_EMAIL_RETRIES}: {exc}"
            log.warning("Email %s → retry %d/%d in %ds", row.message_id, retry_count, MAX_EMAIL_RETRIES, backoff_secs)
        else:
            row.status = "failed"
            row.error_message = f"Max retries exceeded. Last: {exc}"
            row.processed_at = now
        db.commit()


# ── Folder cursor ─────────────────────────────────────────────────────────────


def _get_folder_state(db, account: str, folder: str) -> ImapFolderState:
    state = (
        db.query(ImapFolderState).filter(ImapFolderState.account == account, ImapFolderState.folder == folder).first()
    )
    if state is None:
        state = ImapFolderState(account=account, folder=folder, uidvalidity=0, last_uid=0)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def _sync_new_messages(client: IMAPClient, db, uidvalidity: int) -> int:
    """Ingest messages with UID greater than the stored high-water mark."""
    state = _get_folder_state(db, IMAP_USER, IMAP_FOLDER)
    if state.uidvalidity != uidvalidity:
        if state.uidvalidity:
            log.warning(
                "UIDVALIDITY changed (%d → %d) for %s/%s — resetting cursor; "
                "Message-ID dedup prevents double ingestion",
                state.uidvalidity,
                uidvalidity,
                IMAP_USER,
                IMAP_FOLDER,
            )
        state.uidvalidity = uidvalidity
        state.last_uid = 0
        db.commit()

    # N:* always returns at least the highest-UID message, even when it is < N
    uids = sorted(u for u in client.search(["UID", f"{state.last_uid + 1}:*"]) if u > state.last_uid)
    if not uids:
        return 0

    log.info("Found %d new message(s) past UID %d", len(uids), state.last_uid)
    headers = client.fetch(uids, ["BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])

    processed = 0
    for uid in uids:
        message_id = _header_message_id(headers.get(uid, {}))
        if not message_id:
            # Stable fabricated ID — identical across polls and worker restarts
            message_id = f"<uid-{uidvalidity}-{uid}@{IMAP_USER or 'carange'}>"

        exists = db.query(EmailIngestLog.id).filter(EmailIngestLog.message_id == message_id).first()
        if exists is None:
            data = client.fetch([uid], ["BODY.PEEK[]"])
            raw = _body_from_fetch(data.get(uid, {}))
            if raw is None:
                log.warning("UID %d: empty fetch response — skipping (cursor still advances)", uid)
            else:
                row = _create_log_row(db, message_id, raw)
                log.info("Processing email: %s / %s", row.sender, row.subject)
                _process_row(db, row, raw)
                processed += 1

        # Cursor advances regardless of outcome — retries replay from the DB copy
        state.last_uid = uid
        db.commit()

        try:
            client.add_flags([uid], [SEEN])  # courtesy only; ingestion ignores flags
        except Exception:
            pass
        _touch_liveness()
    return processed


# ── DB-driven retries ─────────────────────────────────────────────────────────


def _process_due_retries(db, limit: int = 10) -> int:
    """Re-run pending rows whose retry_after has passed, replaying the stored raw copy.

    Also reclaims rows stuck in pending with no retry_after (worker crashed
    mid-processing): with a raw copy they are retried immediately, without one
    they are marked failed.
    """
    now = datetime.now(timezone.utc)

    cutoff = now - timedelta(minutes=STUCK_TIMEOUT_MIN)
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
        if row.raw_size:
            log.warning("Reclaiming stuck pending email %d (%s) for retry", row.id, row.message_id)
            row.retry_after = now
        else:
            log.warning("Stuck pending email %d (%s) has no raw copy — marking failed", row.id, row.message_id)
            row.status = "failed"
            row.error_message = "Worker crashed mid-processing and no raw copy was stored"
            row.processed_at = now
    if stuck:
        db.commit()

    due = (
        db.query(EmailIngestLog)
        .filter(
            EmailIngestLog.status == "pending",
            EmailIngestLog.retry_after.isnot(None),
            EmailIngestLog.retry_after <= now,
        )
        .order_by(EmailIngestLog.retry_after)
        .limit(limit)
        .all()
    )

    count = 0
    for row in due:
        blob = row.raw_email  # deferred column — loads here
        if not blob:
            row.status = "failed"
            row.error_message = "No stored raw copy to retry from"
            row.processed_at = now
            db.commit()
            continue
        log.info("Retrying email %s (attempt %d)", row.message_id, (row.retry_count or 0) + 1)
        _process_row(db, row, _decompress(blob))
        count += 1
        _touch_liveness()
    return count


# ── Main loop ─────────────────────────────────────────────────────────────────


def _serve(client: IMAPClient) -> None:
    """Process new mail + retries until the connection drops or config changes."""
    select_info = client.select_folder(IMAP_FOLDER)
    uidvalidity = int(select_info.get(b"UIDVALIDITY", 0))
    can_idle = client.has_capability("IDLE")
    if not can_idle:
        log.info("Server lacks IDLE — falling back to %ds polling", POLL_INTERVAL)
    config_snapshot = (IMAP_HOST, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER)

    while True:
        _touch_liveness()
        with SessionLocal() as db:
            _sync_new_messages(client, db, uidvalidity)
            _process_due_retries(db)
        _record_status("email_worker_last_ok", datetime.now(timezone.utc).isoformat())

        if can_idle:
            client.idle()
            try:
                client.idle_check(timeout=min(POLL_INTERVAL, IDLE_CHECK_SECS))
            finally:
                client.idle_done()
        else:
            time.sleep(min(POLL_INTERVAL, IDLE_CHECK_SECS))

        _load_config()
        if (IMAP_HOST, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER) != config_snapshot:
            log.info("IMAP configuration changed — reconnecting")
            return


def run() -> None:
    log.info("Email worker started — host=%s user=%s folder=%s", IMAP_HOST, IMAP_USER, IMAP_FOLDER)
    consecutive_failures = 0
    while True:
        _touch_liveness()
        _load_config()
        if not IMAP_USER or not IMAP_PASSWORD:
            log.error("IMAP_USER or IMAP_PASSWORD not set — waiting for configuration")
            _record_status("email_worker_last_error", "IMAP credentials not configured")
            time.sleep(30)
            continue

        client = None
        try:
            client = IMAPClient(IMAP_HOST, ssl=True, timeout=IMAP_TIMEOUT)
            client.login(IMAP_USER, IMAP_PASSWORD)
            consecutive_failures = 0
            _record_status("email_worker_last_error", "")
            _serve(client)  # returns only on config change
        except Exception as exc:
            consecutive_failures += 1
            message = f"{type(exc).__name__}: {exc}"
            log.error("IMAP session error (%d consecutive): %s", consecutive_failures, message)
            _record_status("email_worker_last_error", message)
            backoff = min(RECONNECT_BACKOFF_BASE * (2 ** (consecutive_failures - 1)), RECONNECT_BACKOFF_MAX)
            # Stay responsive to the liveness probe while backing off
            while backoff > 0:
                _touch_liveness()
                step = min(backoff, IDLE_CHECK_SECS)
                time.sleep(step)
                backoff -= step
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass


if __name__ == "__main__":
    run()
