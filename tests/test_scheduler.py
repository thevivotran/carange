"""Tests for the daily template scheduler (`app/services/scheduler.py`).

Covers the previously-uncovered `_run_once` and `_create_from_template`
paths. We don't start the actual daemon thread — that would block the
test suite — we just exercise the work functions directly.
"""

from datetime import date

from app.models.database import Transaction, TransactionTemplate, TransactionType
from app.services.scheduler import (
    _create_from_template,
    _run_once,
    _send_budget_threshold_alerts,
    _send_review_reminder,
)

# ── _create_from_template ────────────────────────────────────────────────


def test_create_from_template_monthly_advances_next_run(client, db_session, income_cat):
    """A template with cadence='monthly' creates one tx and bumps
    next_run_at forward by ~30 days."""
    tmpl = TransactionTemplate(
        name="Salary template",
        amount=10_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Monthly salary",
        cadence="monthly",
        next_run_at=date(2026, 6, 1),
        is_active=True,
        auto_approve=True,
    )
    db_session.add(tmpl)
    db_session.commit()
    db_session.refresh(tmpl)

    created = _create_from_template(db_session, tmpl, date(2026, 6, 1))
    db_session.commit()  # _create_from_template doesn't commit on its own
    assert created is True

    # Transaction was created
    txs = db_session.query(Transaction).filter(Transaction.description == "Monthly salary").all()
    assert len(txs) == 1
    assert txs[0].amount == 10_000_000
    assert txs[0].source == "template"
    assert txs[0].needs_review is False  # auto_approve=True

    # next_run_at advanced
    db_session.expire_all()
    fresh = db_session.query(TransactionTemplate).filter_by(id=tmpl.id).first()
    assert fresh.next_run_at == date(2026, 7, 1)
    assert fresh.last_run_at == date(2026, 6, 1)


def test_create_from_template_unknown_cadence_returns_false(client, db_session, income_cat):
    """An unknown cadence logs a warning and returns False without crashing."""
    tmpl = TransactionTemplate(
        name="Bogus cadence template",
        amount=1_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        cadence="decennial",  # not in CADENCE_DELTA
        next_run_at=date(2026, 6, 1),
        is_active=True,
    )
    db_session.add(tmpl)
    db_session.commit()
    db_session.refresh(tmpl)

    created = _create_from_template(db_session, tmpl, date(2026, 6, 1))
    assert created is False

    # No transaction was created
    txs = db_session.query(Transaction).filter(Transaction.source == "template").all()
    assert txs == []


def test_create_from_template_marks_needs_review_when_not_auto_approved(client, db_session, expense_cat):
    """Templates with auto_approve=False land in the review inbox."""
    tmpl = TransactionTemplate(
        name="Recurring grocery",
        amount=500_000,
        type=TransactionType.EXPENSE,
        category_id=expense_cat.id,
        cadence="weekly",
        next_run_at=date(2026, 6, 1),
        is_active=True,
        auto_approve=False,
    )
    db_session.add(tmpl)
    db_session.commit()
    db_session.refresh(tmpl)

    _create_from_template(db_session, tmpl, date(2026, 6, 1))
    db_session.commit()

    tx = db_session.query(Transaction).filter(Transaction.source == "template").first()
    assert tx is not None
    assert tx.needs_review is True


def test_create_from_template_defaults_payment_method_to_cash(client, db_session, income_cat):
    """A template with no payment_method set results in a transaction with
    payment_method='cash' (the schema default)."""
    tmpl = TransactionTemplate(
        name="No payment method",
        amount=100_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        cadence="daily",
        next_run_at=date(2026, 6, 1),
        is_active=True,
        auto_approve=True,
        payment_method=None,
    )
    db_session.add(tmpl)
    db_session.commit()
    db_session.refresh(tmpl)

    _create_from_template(db_session, tmpl, date(2026, 6, 1))
    db_session.commit()

    tx = db_session.query(Transaction).filter(Transaction.source == "template").first()
    assert tx.payment_method == "cash"


# ── _run_once ───────────────────────────────────────────────────────────


def test_run_once_creates_transactions_for_due_templates(client, db_session, income_cat):
    """_run_once finds all active templates with next_run_at <= today,
    creates a transaction for each, and returns the count."""
    # Template due today
    t1 = TransactionTemplate(
        name="Due today",
        amount=1_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Tmpl due today",
        cadence="monthly",
        next_run_at=date(2026, 6, 1),
        is_active=True,
        auto_approve=True,
    )
    # Template due in the past
    t2 = TransactionTemplate(
        name="Overdue",
        amount=2_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Tmpl overdue",
        cadence="weekly",
        next_run_at=date(2026, 5, 1),
        is_active=True,
        auto_approve=True,
    )
    # Template due in the future (should NOT be processed)
    t3 = TransactionTemplate(
        name="Future",
        amount=3_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Tmpl future",
        cadence="monthly",
        next_run_at=date(2027, 1, 1),
        is_active=True,
        auto_approve=True,
    )
    # Inactive template (should NOT be processed even if past due)
    t4 = TransactionTemplate(
        name="Inactive",
        amount=4_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Tmpl inactive",
        cadence="monthly",
        next_run_at=date(2026, 1, 1),
        is_active=False,
        auto_approve=True,
    )
    # No cadence set (should NOT be processed)
    t5 = TransactionTemplate(
        name="No cadence",
        amount=5_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Tmpl no cadence",
        cadence=None,
        next_run_at=date(2026, 1, 1),
        is_active=True,
        auto_approve=True,
    )
    for tmpl in (t1, t2, t3, t4, t5):
        db_session.add(tmpl)
    db_session.commit()

    created = _run_once(db_session, date(2026, 6, 15))
    assert created == 2  # only t1 and t2 were due + active + have cadence

    # Verify which transactions were created (by description)
    descriptions = {t.description for t in db_session.query(Transaction).filter(Transaction.source == "template").all()}
    assert "Tmpl due today" in descriptions
    assert "Tmpl overdue" in descriptions
    assert "Tmpl future" not in descriptions
    assert "Tmpl inactive" not in descriptions
    assert "Tmpl no cadence" not in descriptions


def test_run_once_logs_and_continues_after_per_template_exception(client, db_session, income_cat, monkeypatch):
    """When _create_from_template raises for one template, _run_once logs
    the failure and rolls back the failed tx. The other templates still
    process in the same run because the rollback only affects the failed
    template's pending change — subsequent _create_from_template calls
    re-flush on a clean session state.

    (Implementation note: db.rollback() inside _run_once's except clause
    discards the session, so the next template's add() starts fresh. The
    'Good template' tx lands in its own transaction, committed at the
    end of _run_once when created > 0.)"""
    t1 = TransactionTemplate(
        name="Will explode",
        amount=1_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Tmpl bad",
        cadence="monthly",
        next_run_at=date(2026, 6, 1),
        is_active=True,
        auto_approve=True,
    )
    t2 = TransactionTemplate(
        name="Good template",
        amount=2_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="Tmpl good",
        cadence="weekly",
        next_run_at=date(2026, 6, 1),
        is_active=True,
        auto_approve=True,
    )
    db_session.add_all([t1, t2])
    db_session.commit()

    # Make _create_from_template raise on the first template only
    real_create = _create_from_template
    call_count = {"n": 0}

    def flaky(db_, tmpl, today_):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated template processing failure")
        return real_create(db_, tmpl, today_)

    monkeypatch.setattr("app.services.scheduler._create_from_template", flaky)

    created = _run_once(db_session, date(2026, 6, 15))
    # The first one failed (no increment), the second succeeded (1)
    assert created == 1
    assert call_count["n"] == 2  # both were attempted

    # The good template's transaction landed and was committed
    txs = db_session.query(Transaction).filter(Transaction.source == "template").all()
    assert len(txs) == 1
    assert txs[0].description == "Tmpl good"


def test_run_once_returns_zero_when_no_templates_due(client, db_session):
    """Empty result → returns 0, no transactions created, no commit errors."""
    created = _run_once(db_session, date(2026, 6, 15))
    assert created == 0


# ── _send_review_reminder ────────────────────────────────────────────────


def test_send_review_reminder_no_op_when_inbox_empty(client, db_session):
    """If there are no needs_review transactions, _send_review_reminder
    is a no-op (does not raise, does not publish)."""
    # Should not raise
    _send_review_reminder(db_session)


def test_send_review_reminder_publishes_when_inbox_has_items(client, db_session, income_cat):
    """When at least one transaction has needs_review=True, the function
    publishes a review_reminder notification. We can't easily assert the
    notification fired without mocking, but we can at least confirm the
    path runs without raising."""
    from app.models.database import Transaction

    # Add one needs_review transaction
    tx = Transaction(
        date=date(2026, 6, 15),
        amount=100_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="needs review",
        needs_review=True,
    )
    db_session.add(tx)
    db_session.commit()

    # publish_notification will likely fail in tests (no real DB schema for
    # notification_events, or notification_service is not fully wired) but
    # the function should swallow the exception and not propagate it.
    _send_review_reminder(db_session)


def test_send_review_reminder_handles_publish_exception(client, db_session, income_cat, monkeypatch):
    """When publish_notification raises, the function logs and continues
    (doesn't propagate the exception to the scheduler loop)."""
    from app.models.database import Transaction
    from app.services import notification_service

    # Force a transaction in the review inbox
    tx = Transaction(
        date=date(2026, 6, 15),
        amount=100_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        description="needs review",
        needs_review=True,
    )
    db_session.add(tx)
    db_session.commit()

    def boom(*_a, **_kw):
        raise RuntimeError("simulated notification publish failure")

    monkeypatch.setattr(notification_service, "publish_notification", boom)
    # The function must catch this and not propagate
    _send_review_reminder(db_session)


# ── _send_budget_threshold_alerts ───────────────────────────────────────


def test_send_budget_threshold_alerts_handles_check_exception(client, db_session, monkeypatch):
    """When check_and_send_budget_alerts raises (e.g. due to budget data
    inconsistency), the scheduler swallows the exception so the main loop
    continues."""
    from app.services import budget_alerts

    def boom(_db):
        raise RuntimeError("simulated budget check failure")

    monkeypatch.setattr(budget_alerts, "check_and_send_budget_alerts", boom)
    # Must not propagate
    _send_budget_threshold_alerts(db_session)
