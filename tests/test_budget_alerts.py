"""Tests for budget threshold alert scheduler job (app/services/budget_alerts.py)."""

from datetime import date

import pytest

from app.models.database import BudgetAllocation, Category, NotificationEvent, Transaction, TransactionType
from app.services.budget_alerts import check_and_send_budget_alerts
from app.services.settings_service import set_setting


@pytest.fixture()
def food_cat(db_session):
    cat = Category(name="Food", type=TransactionType.EXPENSE, color="#EF4444", icon="utensils")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


@pytest.fixture()
def transport_cat(db_session):
    cat = Category(name="Transport", type=TransactionType.EXPENSE, color="#F59E0B", icon="car")
    db_session.add(cat)
    db_session.commit()
    db_session.refresh(cat)
    return cat


def _add_alloc(db, category_id, year_month, amount):
    a = BudgetAllocation(category_id=category_id, year_month=year_month, amount=amount)
    db.add(a)
    db.commit()
    return a


def _add_expense(db, category_id, date_val, amount):
    t = Transaction(
        date=date_val,
        amount=amount,
        type=TransactionType.EXPENSE,
        category_id=category_id,
    )
    db.add(t)
    db.commit()
    return t


def _current_ym():
    today = date.today()
    return f"{today.year:04d}-{today.month:02d}"


def _setup_telegram(db_session):
    set_setting(db_session, "telegram_bot_token", "tok")
    set_setting(db_session, "telegram_chat_id", "123")
    set_setting(db_session, "telegram_budget_alerts_enabled", "true")


def _count_budget_alert_events(db_session):
    return db_session.query(NotificationEvent).filter(NotificationEvent.event_type == "budget_alert").count()


def test_under_80_percent_no_alert(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 1_000_000)
    _setup_telegram(db_session)

    check_and_send_budget_alerts(db_session)
    assert _count_budget_alert_events(db_session) == 0


def test_crossing_80_percent_sends_alert(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 4_500_000)
    _setup_telegram(db_session)

    check_and_send_budget_alerts(db_session)
    assert _count_budget_alert_events(db_session) == 1


def test_re_run_does_not_duplicate_alert(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 4_500_000)
    _setup_telegram(db_session)

    check_and_send_budget_alerts(db_session)
    assert _count_budget_alert_events(db_session) == 1

    check_and_send_budget_alerts(db_session)
    assert _count_budget_alert_events(db_session) == 1


def test_crossing_100_percent_sends_second_alert(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 4_500_000)
    _setup_telegram(db_session)

    check_and_send_budget_alerts(db_session)
    assert _count_budget_alert_events(db_session) == 1

    _add_expense(db_session, food_cat.id, date.today(), 1_000_000)

    check_and_send_budget_alerts(db_session)
    assert _count_budget_alert_events(db_session) == 2


def test_disabled_setting_no_alerts(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 4_500_000)
    set_setting(db_session, "telegram_bot_token", "tok")
    set_setting(db_session, "telegram_chat_id", "123")
    set_setting(db_session, "telegram_budget_alerts_enabled", "false")

    check_and_send_budget_alerts(db_session)
    assert _count_budget_alert_events(db_session) == 0


def test_unbudgeted_category_never_alerts(db_session, food_cat):
    _add_expense(db_session, food_cat.id, date.today(), 10_000_000)
    _setup_telegram(db_session)

    check_and_send_budget_alerts(db_session)
    assert _count_budget_alert_events(db_session) == 0
