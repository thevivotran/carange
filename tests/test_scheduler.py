"""Tests for the recurring template scheduler (app/services/scheduler.py)."""

from datetime import date, timedelta

import pytest

from app.models.database import Category, Transaction, TransactionTemplate, TransactionType
from app.services.scheduler import _run_once


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
    """Override the global _no_seed: suppress seeding but keep real start_scheduler.

    Tests in this file call start_scheduler() directly, so the global no-op
    patch from conftest must not apply here.
    """
    import main

    monkeypatch.setattr(main, "seed_default_categories", lambda: None)


@pytest.fixture()
def expense_cat(db_session):
    cat = Category(name="Rent", type=TransactionType.EXPENSE, color="#EF4444", icon="home")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def _make_template(db, cat, *, cadence, next_run_at, auto_approve=False, is_active=True):
    t = TransactionTemplate(
        name="Test Template",
        amount=1_000_000,
        type=TransactionType.EXPENSE,
        category_id=cat.id,
        cadence=cadence,
        next_run_at=next_run_at,
        auto_approve=auto_approve,
        is_active=is_active,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def test_due_template_creates_transaction(db_session, expense_cat):
    today = date.today()
    _make_template(db_session, expense_cat, cadence="monthly", next_run_at=today)

    count = _run_once(db_session, today)

    assert count == 1
    txs = db_session.query(Transaction).filter(Transaction.source == "template").all()
    assert len(txs) == 1
    assert txs[0].date == today
    assert txs[0].amount == 1_000_000


def test_due_template_auto_approve_false_sets_needs_review(db_session, expense_cat):
    today = date.today()
    _make_template(db_session, expense_cat, cadence="monthly", next_run_at=today, auto_approve=False)

    _run_once(db_session, today)

    tx = db_session.query(Transaction).filter(Transaction.source == "template").first()
    assert tx.needs_review is True


def test_due_template_auto_approve_true_skips_review(db_session, expense_cat):
    today = date.today()
    _make_template(db_session, expense_cat, cadence="monthly", next_run_at=today, auto_approve=True)

    _run_once(db_session, today)

    tx = db_session.query(Transaction).filter(Transaction.source == "template").first()
    assert tx.needs_review is False


def test_future_template_not_triggered(db_session, expense_cat):
    tomorrow = date.today() + timedelta(days=1)
    _make_template(db_session, expense_cat, cadence="monthly", next_run_at=tomorrow)

    count = _run_once(db_session, date.today())

    assert count == 0
    assert db_session.query(Transaction).filter(Transaction.source == "template").count() == 0


def test_inactive_template_not_triggered(db_session, expense_cat):
    today = date.today()
    _make_template(db_session, expense_cat, cadence="monthly", next_run_at=today, is_active=False)

    count = _run_once(db_session, today)

    assert count == 0


def test_daily_cadence_advances_next_run_at(db_session, expense_cat):
    today = date.today()
    tmpl = _make_template(db_session, expense_cat, cadence="daily", next_run_at=today)

    _run_once(db_session, today)

    db_session.refresh(tmpl)
    assert tmpl.next_run_at == today + timedelta(days=1)
    assert tmpl.last_run_at == today


def test_weekly_cadence_advances_next_run_at(db_session, expense_cat):
    today = date.today()
    tmpl = _make_template(db_session, expense_cat, cadence="weekly", next_run_at=today)

    _run_once(db_session, today)

    db_session.refresh(tmpl)
    assert tmpl.next_run_at == today + timedelta(weeks=1)


def test_monthly_cadence_advances_next_run_at(db_session, expense_cat):
    today = date(2026, 1, 15)
    tmpl = _make_template(db_session, expense_cat, cadence="monthly", next_run_at=today)

    _run_once(db_session, today)

    db_session.refresh(tmpl)
    assert tmpl.next_run_at == date(2026, 2, 15)


def test_yearly_cadence_advances_next_run_at(db_session, expense_cat):
    today = date(2026, 3, 10)
    tmpl = _make_template(db_session, expense_cat, cadence="yearly", next_run_at=today)

    _run_once(db_session, today)

    db_session.refresh(tmpl)
    assert tmpl.next_run_at == date(2027, 3, 10)


def test_unknown_cadence_skips_template(db_session, expense_cat):
    today = date.today()
    _make_template(db_session, expense_cat, cadence="biweekly", next_run_at=today)

    count = _run_once(db_session, today)

    assert count == 0
    assert db_session.query(Transaction).filter(Transaction.source == "template").count() == 0


def test_overdue_template_runs_once_and_advances(db_session, expense_cat):
    """A template that is 5 days overdue fires once and advances by one interval."""
    five_days_ago = date.today() - timedelta(days=5)
    tmpl = _make_template(db_session, expense_cat, cadence="monthly", next_run_at=five_days_ago)

    count = _run_once(db_session, date.today())

    assert count == 1
    db_session.refresh(tmpl)
    assert tmpl.next_run_at > five_days_ago


def test_start_scheduler_returns_thread(db_session):
    from app.services.scheduler import start_scheduler

    t = start_scheduler()
    assert t.is_alive()
    assert t.daemon is True


def test_no_cadence_template_not_triggered(db_session, expense_cat):
    today = date.today()
    tmpl = TransactionTemplate(
        name="Manual Only",
        amount=500_000,
        type=TransactionType.EXPENSE,
        category_id=expense_cat.id,
        cadence=None,
        next_run_at=None,
        is_active=True,
    )
    db_session.add(tmpl)
    db_session.commit()

    count = _run_once(db_session, today)

    assert count == 0


def test_scheduler_sends_review_reminder(db_session):
    from datetime import date as _date

    from app.models.database import NotificationEvent
    from app.services.scheduler import _send_review_reminder

    cat = Category(name="Misc", type=TransactionType.EXPENSE, color="#000", icon="tag")
    db_session.add(cat)
    db_session.commit()

    tx = Transaction(
        date=_date(2026, 1, 1),
        amount=10_000,
        type=TransactionType.EXPENSE,
        category_id=cat.id,
        payment_method="cash",
        needs_review=True,
    )
    db_session.add(tx)
    db_session.commit()

    _send_review_reminder(db_session)

    evt = db_session.query(NotificationEvent).filter(NotificationEvent.event_type == "review_reminder").first()
    assert evt is not None
    assert evt.payload["count"] == 1


def test_scheduler_skips_review_reminder_when_empty(db_session):
    from app.models.database import NotificationEvent
    from app.services.scheduler import _send_review_reminder

    _send_review_reminder(db_session)

    count = db_session.query(NotificationEvent).filter(NotificationEvent.event_type == "review_reminder").count()
    assert count == 0


def test_scheduler_sends_budget_threshold_alerts(db_session):
    from datetime import date as _date

    from app.models.database import BudgetAllocation, NotificationEvent
    from app.services.scheduler import _send_budget_threshold_alerts
    from app.services.settings_service import set_setting

    cat = Category(name="AlertCat", type=TransactionType.EXPENSE, color="#000", icon="tag")
    db_session.add(cat)
    db_session.commit()

    today = _date.today()
    ym = f"{today.year:04d}-{today.month:02d}"
    db_session.add(BudgetAllocation(category_id=cat.id, year_month=ym, amount=5_000_000))
    db_session.commit()

    tx = Transaction(
        date=today,
        amount=4_500_000,
        type=TransactionType.EXPENSE,
        category_id=cat.id,
        payment_method="cash",
    )
    db_session.add(tx)
    db_session.commit()

    set_setting(db_session, "telegram_bot_token", "tok")
    set_setting(db_session, "telegram_chat_id", "123")
    set_setting(db_session, "telegram_budget_alerts_enabled", "true")

    _send_budget_threshold_alerts(db_session)

    evt = db_session.query(NotificationEvent).filter(NotificationEvent.event_type == "budget_alert").first()
    assert evt is not None
    assert evt.payload["category_name"] == "AlertCat"
