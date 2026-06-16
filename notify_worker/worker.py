"""
Notify worker — processes notification_events one at a time.

Uses LISTEN/NOTIFY for instant wake-up when a new event is queued, plus
SELECT FOR UPDATE SKIP LOCKED for safe concurrent claiming (PostgreSQL only).

Environment variables:
  DATABASE_URL         connection string      default: postgresql://carange:carange@localhost:5432/carange
  STUCK_TIMEOUT_MIN    minutes before a PROCESSING event is reclaimed  default: 30
  MAX_RETRIES          attempts before permanent failure              default: 3
"""

import logging
import os
import pathlib
import select
import threading
import time
from datetime import datetime, timedelta, timezone

LIVENESS_FILE = pathlib.Path("/tmp/worker_alive")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.models.database import NotificationEvent, NotificationEventStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("notify_worker")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://carange:carange@localhost:5432/carange")
STUCK_TIMEOUT_MINUTES = int(os.getenv("STUCK_TIMEOUT_MIN", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))


# ── Liveness heartbeat ────────────────────────────────────────────────────────

_HEARTBEAT_INTERVAL = 30.0
_last_progress = time.monotonic()


def _mark_progress() -> None:
    global _last_progress
    _last_progress = time.monotonic()


def _start_heartbeat() -> None:
    def beat():
        while True:
            if time.monotonic() - _last_progress < STUCK_TIMEOUT_MINUTES * 60:
                LIVENESS_FILE.touch()
            time.sleep(_HEARTBEAT_INTERVAL)

    threading.Thread(target=beat, daemon=True, name="liveness-heartbeat").start()


def _psycopg2_dsn(url: str) -> str:
    """Strip SQLAlchemy dialect prefix so psycopg2 can parse the URL."""
    return url.replace("postgresql+psycopg2://", "postgresql://").replace("postgres://", "postgresql://")


def _make_session_factory():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ── Event claiming ────────────────────────────────────────────────────────────


def _claim_next(db: Session) -> NotificationEvent | None:
    """PostgreSQL: atomic claim using SELECT FOR UPDATE SKIP LOCKED.

    Also reclaims events stuck in PROCESSING based on started_at, then claims
    the oldest PENDING event not waiting for a retry_after backoff.
    """
    now = datetime.now(timezone.utc)
    stuck_cutoff = now - timedelta(minutes=STUCK_TIMEOUT_MINUTES)

    db.execute(
        text("""
            UPDATE notification_events
               SET status = 'failed', started_at = NULL,
                   error_msg = 'Permanent failure: stuck in processing after max retries'
             WHERE status = 'processing'
               AND started_at < :cutoff
               AND retry_count >= :max_retries
        """),
        {"cutoff": stuck_cutoff, "max_retries": MAX_RETRIES},
    )

    db.execute(
        text("""
            UPDATE notification_events
               SET status = 'pending', started_at = NULL,
                   retry_count = COALESCE(retry_count, 0) + 1
             WHERE status = 'processing'
               AND started_at < :cutoff
        """),
        {"cutoff": stuck_cutoff},
    )

    row = db.execute(
        text("""
            UPDATE notification_events
               SET status = 'processing', started_at = :now
             WHERE id = (
                 SELECT id FROM notification_events
                  WHERE status = 'pending'
                    AND (retry_after IS NULL OR retry_after <= :now)
                  ORDER BY created_at ASC
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
             )
             RETURNING id
        """),
        {"now": now},
    ).fetchone()

    db.commit()

    if row is None:
        return None

    return db.query(NotificationEvent).filter(NotificationEvent.id == row[0]).first()


# ── Failure handling with exponential backoff ─────────────────────────────────


def _handle_failure(db: Session, evt: NotificationEvent, reason: str) -> None:
    """Retry up to MAX_RETRIES with exponential backoff; then permanently fail."""
    retry_count = (evt.retry_count or 0) + 1
    if retry_count <= MAX_RETRIES:
        evt.status = NotificationEventStatus.PENDING
        evt.retry_count = retry_count
        evt.retry_after = datetime.now(timezone.utc) + timedelta(minutes=2 * (2**retry_count))
        evt.error_msg = f"Retry {retry_count}/{MAX_RETRIES}: {reason}"
        db.commit()
        log.warning(
            "Event %d → retry %d/%d: %s",
            evt.id,
            retry_count,
            MAX_RETRIES,
            reason,
        )
    else:
        evt.status = NotificationEventStatus.FAILED
        evt.error_msg = f"Permanent failure after {MAX_RETRIES} retries. Last: {reason}"
        db.commit()
        log.error("Event %d FAILED permanently after %d retries: %s", evt.id, MAX_RETRIES, reason)


# ── Message building ──────────────────────────────────────────────────────────


def _build_message(evt: NotificationEvent, cfg: dict) -> str | None:
    """Dispatch on evt.event_type and build the Telegram message text."""
    from app.notify.telegram import _esc, _amount, _review_link, _transactions_footer, _budget_link

    payload = evt.payload

    if evt.event_type == "advance_ping":
        amount_str = f"{float(payload['amount']):,.0f}đ"
        verb = "Created" if payload["action"] == "created" else "Updated"
        hide = cfg.get("telegram_hide_amounts") == "true"
        footer = _transactions_footer(cfg["app_url"], "Pending settlement — view transactions", "?advance=unsettled")
        text = (
            f"💳 <b>Personal advance — {_esc(verb)}</b>\n"
            f"-{_amount(amount_str, hide)} — {_esc(payload['cat_name'])}\n"
            f"<i>{_esc(payload['description'])}</i>\n{footer}"
        )
        return text

    elif evt.event_type == "tx_ingested":
        amount_str = f"{float(payload['amount']):,.0f}đ"
        direction = "+" if payload["tx_type"] == "income" else "-"
        hide = cfg.get("telegram_hide_amounts") == "true"
        source_label = {"email": "Email", "ocr": "OCR"}.get(payload["source"], payload["source"])
        amt = _amount(direction + amount_str, hide)
        if payload["needs_review"]:
            text = (
                f"⚠️ <b>Needs review</b> [{_esc(source_label)}]\n"
                f"{amt} — {_esc(payload['cat_name'])}\n"
                f"<i>{_esc(payload['description'])}</i>\n{_review_link(cfg['app_url'])}"
            )
        else:
            text = (
                f"💸 <b>New</b> [{_esc(source_label)}]\n"
                f"{amt} — {_esc(payload['cat_name'])}\n"
                f"<i>{_esc(payload['description'])}</i>"
            )
        return text

    elif evt.event_type == "review_reminder":
        count = payload["count"]
        if count <= 0:
            return None
        plural = "s" if count != 1 else ""
        text = f"📋 <b>{count} transaction{plural}</b> pending review.\n{_review_link(cfg['app_url'])}"
        return text

    elif evt.event_type == "budget_alert":
        hide = cfg.get("telegram_hide_amounts") == "true"
        spent_str = f"{payload['spent']:,.0f}đ"
        limit_str = f"{payload['limit']:,.0f}đ"
        status_line = "Over budget!" if payload["threshold"] >= 100 else "Approaching budget limit"
        text = (
            f"🚨 <b>Budget Alert</b> — {_esc(payload['category_name'])}\n"
            f"{_amount(spent_str, hide)} / {_amount(limit_str, hide)} (<b>{payload['pct']:.0f}%</b>)\n"
            f"{status_line}\n{_budget_link(cfg['app_url'])}"
        )
        return text

    else:
        log.warning("Unknown event_type: %s", evt.event_type)
        return None


# ── Run loops ─────────────────────────────────────────────────────────────────


def _process_one(SessionFactory) -> bool:
    """Claim and process one event. Returns True if an event was processed."""
    from app.services.settings_service import get_telegram_config
    from app.notify.telegram import _send

    with SessionFactory() as db:
        evt = _claim_next(db)
        if evt is None:
            return False

        log.info("Claimed event %d (type=%s)", evt.id, evt.event_type)
        _mark_progress()
        try:
            cfg = get_telegram_config(db)
            text = _build_message(evt, cfg)
            if text is None:
                evt.status = NotificationEventStatus.DONE
                db.commit()
            else:
                ok = _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])
                if ok:
                    evt.status = NotificationEventStatus.DONE
                    db.commit()
                else:
                    _handle_failure(db, evt, "Telegram API returned non-OK response")
        except Exception as exc:
            _handle_failure(db, evt, f"Unexpected error: {exc}")
        _mark_progress()
    return True


def _drain_queue(SessionFactory) -> None:
    """Process all available events until the queue is empty."""
    while _process_one(SessionFactory):
        pass


def _run_postgres(SessionFactory) -> None:
    """PostgreSQL mode: LISTEN 'telegram_notifications' + FOR UPDATE SKIP LOCKED."""
    import psycopg2
    import psycopg2.extensions

    dsn = _psycopg2_dsn(DATABASE_URL)

    def _make_listen_conn():
        conn = psycopg2.connect(dsn)
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        conn.cursor().execute("LISTEN telegram_notifications")
        return conn

    log.info("Notify worker: LISTEN/NOTIFY mode (PostgreSQL)")
    listen_conn = _make_listen_conn()

    while True:
        _mark_progress()

        _drain_queue(SessionFactory)

        try:
            readable, _, _ = select.select([listen_conn], [], [], 30.0)
            if readable:
                listen_conn.poll()
                listen_conn.notifies.clear()
        except Exception as exc:
            log.warning("LISTEN connection error (%s) — reconnecting in 5s", exc)
            try:
                listen_conn.close()
            except Exception:
                pass
            time.sleep(5)
            try:
                listen_conn = _make_listen_conn()
                log.info("LISTEN connection restored")
            except Exception as reconnect_exc:
                log.error("Reconnect failed: %s — will retry next loop", reconnect_exc)


def run() -> None:
    log.info("Notify worker starting (DATABASE_URL=%s)", DATABASE_URL)
    _start_heartbeat()
    LIVENESS_FILE.touch()
    SessionFactory = _make_session_factory()
    _run_postgres(SessionFactory)


if __name__ == "__main__":
    run()
