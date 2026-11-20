"""Is auto-import working? A status per integration, built only from what the workers
already record in the database (the workers run in their own containers, so their
liveness files aren't visible here).

status: "ok" | "warning" | "error" | "not_configured"
  error   — the latest attempt failed and nothing has succeeded in the last 24 h
  warning — the latest attempt failed, but something succeeded recently
  ok      — configured, and the latest attempt (if any) succeeded
"""

from __future__ import annotations

import imaplib
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.database import (
    EmailIngestLog,
    ImportJob,
    ImportJobStatus,
    NotificationEvent,
    NotificationEventStatus,
)
from app.services.settings_service import get_setting

STALE_AFTER = timedelta(hours=24)
# Work left waiting this long means the worker isn't running (or can't keep up).
STUCK_AFTER = timedelta(hours=1)
IMAP_TIMEOUT_SECONDS = 10


def _setting(db: Session, key: str, env: str, default: str = "") -> str:
    return get_setting(db, key, os.getenv(env, default)) or ""


def _status(configured: bool, last_success, last_failure, now: datetime) -> str:
    if not configured:
        return "not_configured"
    if last_failure and (not last_success or last_failure > last_success):
        if last_success and now - last_success <= STALE_AFTER:
            return "warning"
        return "error"
    return "ok"


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_iso(value: str):
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _stuck(db: Session, model, pending_status, now: datetime, what: str):
    """(failure_at, message) when items have sat in pending past STUCK_AFTER, else (None, None).
    A down worker never writes a failed row, so a backlog is the only sign of it."""
    cutoff = now - STUCK_AFTER
    query = db.query(model).filter(model.status == pending_status, model.created_at < cutoff)
    if hasattr(model, "retry_after"):  # a scheduled retry isn't stuck
        query = query.filter((model.retry_after.is_(None)) | (model.retry_after <= now))
    count = query.count()
    if not count:
        return None, None
    return now, f"{count} {what} waiting for over an hour — is the worker running?"


def _entry(name: str, label: str, configured: bool, success, failure, error, now: datetime) -> dict:
    success, failure = _aware(success), _aware(failure)
    return {
        "name": name,
        "label": label,
        "configured": configured,
        "last_success_at": success,
        "last_failure_at": failure,
        "last_error": error if failure else None,
        "status": _status(configured, success, failure, now),
    }


def get_integration_health(db: Session, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)

    # Email: the email worker logs every message it processes.
    email_configured = bool(_setting(db, "imap_user", "IMAP_USER") and _setting(db, "imap_password", "IMAP_PASSWORD"))
    email_ok = (
        db.query(EmailIngestLog).filter(EmailIngestLog.status == "done").order_by(EmailIngestLog.id.desc()).first()
    )
    email_fail = (
        db.query(EmailIngestLog).filter(EmailIngestLog.status == "failed").order_by(EmailIngestLog.id.desc()).first()
    )

    # Screenshot OCR: every upload becomes an import job the OCR worker processes.
    ocr_ok = db.query(ImportJob).filter(ImportJob.status == ImportJobStatus.DONE).order_by(ImportJob.id.desc()).first()
    ocr_fail = (
        db.query(ImportJob).filter(ImportJob.status == ImportJobStatus.FAILED).order_by(ImportJob.id.desc()).first()
    )

    # Telegram: the notify worker drains notification events.
    tg_configured = bool(
        _setting(db, "telegram_bot_token", "TELEGRAM_BOT_TOKEN")
        and _setting(db, "telegram_chat_id", "TELEGRAM_CHAT_ID")
    )
    tg_ok = (
        db.query(NotificationEvent)
        .filter(NotificationEvent.status == NotificationEventStatus.DONE)
        .order_by(NotificationEvent.id.desc())
        .first()
    )
    tg_fail = (
        db.query(NotificationEvent)
        .filter(NotificationEvent.status == NotificationEventStatus.FAILED)
        .order_by(NotificationEvent.id.desc())
        .first()
    )

    # The worker's own connection status: a rejected login or unreachable server writes
    # no log row, only email_worker_last_error / email_worker_last_ok.
    email_success = _aware(email_ok and (email_ok.processed_at or email_ok.created_at))
    email_failure = _aware(email_fail and (email_fail.processed_at or email_fail.created_at))
    email_error = email_fail and email_fail.error_message
    worker_ok = _aware(_parse_iso(get_setting(db, "email_worker_last_ok", "") or ""))
    if worker_ok and (not email_success or worker_ok > email_success):
        email_success = worker_ok
    worker_error = get_setting(db, "email_worker_last_error", "") or ""
    if email_configured and worker_error:
        email_failure, email_error = now, worker_error
    elif email_configured and worker_ok and now - worker_ok > STUCK_AFTER:
        email_failure, email_error = now, "The email worker hasn't checked the inbox for over an hour"

    ocr_success = ocr_ok and (ocr_ok.processed_at or ocr_ok.created_at)
    ocr_failure = ocr_fail and (ocr_fail.processed_at or ocr_fail.created_at)
    ocr_error = ocr_fail and ocr_fail.error_message
    stuck_at, stuck_msg = _stuck(db, ImportJob, ImportJobStatus.PENDING, now, "screenshot(s)")
    if stuck_at:
        ocr_failure, ocr_error = stuck_at, stuck_msg

    tg_success = tg_ok and (tg_ok.started_at or tg_ok.created_at)
    tg_failure = tg_fail and (tg_fail.started_at or tg_fail.created_at)
    tg_error = tg_fail and tg_fail.error_msg
    stuck_at, stuck_msg = _stuck(db, NotificationEvent, NotificationEventStatus.PENDING, now, "notification(s)")
    if tg_configured and stuck_at:
        tg_failure, tg_error = stuck_at, stuck_msg

    return [
        _entry("email", "Bank email import", email_configured, email_success, email_failure, email_error, now),
        _entry(
            "ocr",
            "Screenshot reading (OCR)",
            True,  # uploads always work; the worker either reads them or records why not
            ocr_success,
            ocr_failure,
            ocr_error,
            now,
        ),
        _entry("telegram", "Telegram notifications", tg_configured, tg_success, tg_failure, tg_error, now),
    ]


def any_integration_failing(db: Session) -> bool:
    return any(i["status"] == "error" for i in get_integration_health(db))


def check_imap_login(db: Session) -> tuple[bool, str]:
    """Log in to the saved IMAP account and select the folder. Never returns the password."""
    host = _setting(db, "imap_host", "IMAP_HOST", "imap.gmail.com")
    user = _setting(db, "imap_user", "IMAP_USER")
    password = _setting(db, "imap_password", "IMAP_PASSWORD")
    folder = _setting(db, "imap_folder", "IMAP_FOLDER", "INBOX")
    if not user or not password:
        return False, "Add the email address and app password first"
    try:
        conn = imaplib.IMAP4_SSL(host, timeout=IMAP_TIMEOUT_SECONDS)
        try:
            conn.login(user, password)
            status, _ = conn.select(folder, readonly=True)
            if status != "OK":
                return False, f"Logged in, but folder '{folder}' was not found"
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 — best effort
                pass
    except imaplib.IMAP4.error:
        return False, "Login failed — check the email address and app password"
    except OSError as exc:
        return False, f"Could not reach {host}: {exc.__class__.__name__}"
    return True, f"Connected to {host} as {user}"
