"""Daily background scheduler for recurring transaction templates.

Runs as a daemon thread started from main.py lifespan. Each run:
  - Finds active templates with cadence set and next_run_at <= today
  - Creates a Transaction (auto-approved or needs_review based on template.auto_approve)
  - Advances next_run_at by the cadence interval
  - Respects lead_days: creates the transaction lead_days before next_run_at
"""

import logging
import threading
import time
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from sqlalchemy.orm import Session

from app.models.database import SessionLocal, Transaction, TransactionTemplate

log = logging.getLogger("app.scheduler")

_CADENCE_DELTA = {
    "daily": lambda d: d + timedelta(days=1),
    "weekly": lambda d: d + timedelta(weeks=1),
    "monthly": lambda d: d + relativedelta(months=1),
    "yearly": lambda d: d + relativedelta(years=1),
}

_CHECK_INTERVAL_SECONDS = 3600  # wake up every hour; actual work runs once per day


def _run_once(db: Session, today: date) -> int:
    """Process all templates due today. Returns count of transactions created."""
    templates = (
        db.query(TransactionTemplate)
        .filter(
            TransactionTemplate.is_active == True,  # noqa: E712
            TransactionTemplate.cadence.isnot(None),
            TransactionTemplate.next_run_at.isnot(None),
            TransactionTemplate.next_run_at <= today,
        )
        .all()
    )

    created = 0
    for tmpl in templates:
        try:
            if _create_from_template(db, tmpl, today):
                created += 1
        except Exception:
            log.exception("Scheduler: failed to create transaction from template %d", tmpl.id)
            db.rollback()

    if created:
        db.commit()
        log.info("Scheduler: created %d recurring transaction(s)", created)
    return created


def _create_from_template(db: Session, tmpl: TransactionTemplate, today: date) -> bool:
    """Create a transaction from a recurring template and advance next_run_at.

    Returns True when a transaction was created, False when skipped.
    """
    advance_fn = _CADENCE_DELTA.get(tmpl.cadence)
    if advance_fn is None:
        log.warning("Scheduler: unknown cadence %r for template %d — skipping", tmpl.cadence, tmpl.id)
        return False

    # The transaction date is next_run_at, regardless of lead_days
    tx_date = tmpl.next_run_at

    tx = Transaction(
        date=tx_date,
        amount=tmpl.amount,
        type=tmpl.type,
        category_id=tmpl.category_id,
        description=tmpl.description,
        payment_method=tmpl.payment_method or "cash",
        source="template",
        needs_review=not tmpl.auto_approve,
    )
    db.add(tx)
    db.flush()

    # Advance next_run_at
    new_next = advance_fn(tmpl.next_run_at)
    tmpl.last_run_at = today
    tmpl.next_run_at = new_next

    log.debug(
        "Scheduler: template %d '%s' → tx %d on %s; next_run_at=%s",
        tmpl.id,
        tmpl.name,
        tx.id,
        tx_date,
        new_next,
    )
    return True


def _scheduler_loop() -> None:
    """Daemon loop: check once per _CHECK_INTERVAL_SECONDS, run templates due today."""
    log.info("Scheduler: background thread started")
    last_run_date: date | None = None

    while True:
        try:
            today = date.today()
            if last_run_date != today:
                db: Session = SessionLocal()
                try:
                    _run_once(db, today)
                    last_run_date = today
                finally:
                    db.close()
        except Exception:
            log.exception("Scheduler: unexpected error in main loop")

        time.sleep(_CHECK_INTERVAL_SECONDS)


def start_scheduler() -> threading.Thread:
    """Start the scheduler daemon thread. Call once at app startup."""
    t = threading.Thread(target=_scheduler_loop, name="template-scheduler", daemon=True)
    t.start()
    return t
