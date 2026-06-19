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


def _build_message(evt: NotificationEvent, cfg: dict, db: Session | None = None) -> tuple[str | None, dict | None]:
    """Dispatch on evt.event_type and build the Telegram message text + optional reply_markup."""
    from app.notify.telegram import _esc, _amount, _review_link, _transactions_footer, _budget_link, inline_url_keyboard
    from app.services.budget_context import render_bar

    payload = evt.payload
    app_url = cfg.get("app_url", "")

    if evt.event_type == "advance_ping":
        amount_str = f"{float(payload['amount']):,.0f}đ"
        verb = "Created" if payload["action"] == "created" else "Updated"
        hide = cfg.get("telegram_hide_amounts") == "true"
        footer = _transactions_footer(cfg["app_url"], "Pending settlement — view transactions", "?advance=unsettled")
        text = (
            f"💳 <b>Personal advance — {_esc(verb)}</b>\n"
            f"———\n"
            f"-{_amount(amount_str, hide)} — {_esc(payload['cat_name'])}\n"
            f"<i>{_esc(payload['description'])}</i>\n{footer}"
        )
        markup = inline_url_keyboard(
            app_url,
            [
                ("📌 View advances", "/transactions?advance=unsettled"),
            ],
        )
        return text, markup

    elif evt.event_type == "tx_ingested":
        from app.notify.telegram import _budget_bar_line

        amount_str = f"{float(payload['amount']):,.0f}đ"
        direction = "+" if payload["tx_type"] == "income" else "-"
        hide = cfg.get("telegram_hide_amounts") == "true"
        source_label = {"email": "Email", "ocr": "OCR"}.get(payload["source"], payload["source"])
        amt_line = _amount(f"{direction}{amount_str} — {_esc(payload['cat_name'])}", hide)
        tx_id = payload.get("tx_id")

        # Fetch budget snapshot for expense transactions when DB is available
        snap = None
        is_expense = payload.get("tx_type") == "expense"
        if db is not None and is_expense and payload.get("category_id") and payload.get("date"):
            try:
                from app.services.budget_context import budget_snapshot
                from app.services.fiscal_period import current_period_label, get_month_start_day
                from datetime import date as _date

                _day = get_month_start_day(db)
                _tx_date = _date.fromisoformat(payload["date"])
                _label = current_period_label(_tx_date, _day)
                snap = budget_snapshot(db, payload["category_id"], _label, day=_day)
            except Exception as exc:
                log.warning("Budget snapshot failed for tx_ingested event %d: %s", evt.id, exc)

        bar_line = ("\n" + _budget_bar_line(snap)) if snap else ""

        if payload["needs_review"]:
            header = f"⚠️ <b>Needs review [{_esc(source_label)}]</b>"
            text = f"{header}\n———\n<b>{amt_line}</b>\n<i>{_esc(payload['description'])}</i>{bar_line}"
            keyboard_items = [
                ("📥 Review inbox", "/transactions?needs_review=true"),
            ]
        else:
            header = f"💸 <b>New [{_esc(source_label)}]</b>"
            text = f"{header}\n———\n<b>{amt_line}</b>\n<i>{_esc(payload['description'])}</i>{bar_line}"
            keyboard_items = []
            if tx_id:
                keyboard_items.append(("🔍 View", f"/transactions?focus={tx_id}"))
            if is_expense:
                keyboard_items.append(("📊 View budget", "/budget"))
        markup = inline_url_keyboard(app_url, keyboard_items)
        return text, markup

    elif evt.event_type == "review_reminder":
        count = payload["count"]
        if count <= 0:
            return None, None
        plural = "s" if count != 1 else ""
        text = f"📋 <b>{count} transaction{plural}</b> pending review.\n{_review_link(cfg['app_url'])}"
        markup = inline_url_keyboard(
            app_url,
            [
                ("📥 Review inbox", "/transactions?needs_review=true"),
            ],
        )
        return text, markup

    elif evt.event_type == "budget_alert":
        hide = cfg.get("telegram_hide_amounts") == "true"
        spent_str = f"{payload['spent']:,.0f}đ"
        limit_str = f"{payload['limit']:,.0f}đ"
        pct = payload["pct"]
        bar = render_bar(pct)
        status_line = "Over budget!" if pct >= 100 else "Approaching budget limit"
        text = (
            f"🚨 <b>Budget Alert</b> — {_esc(payload['category_name'])}\n"
            f"———\n"
            f"{bar} {pct:.0f}%\n"
            f"{_amount(spent_str, hide)} / {_amount(limit_str, hide)}\n"
            f"{status_line}\n{_budget_link(cfg['app_url'])}"
        )
        markup = inline_url_keyboard(
            app_url,
            [
                ("📊 View budget", "/budget"),
            ],
        )
        return text, markup

    else:
        log.warning("Unknown event_type: %s", evt.event_type)
        return None, None


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
            text, reply_markup = _build_message(evt, cfg, db)
            if text is None:
                evt.status = NotificationEventStatus.DONE
                db.commit()
            else:
                ok = _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"], reply_markup=reply_markup)
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
