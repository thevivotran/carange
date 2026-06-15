"""Tests for budget threshold alert scheduler job (app/services/budget_alerts.py)."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.models.database import BudgetAllocation, Category, Transaction, TransactionType
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


def test_under_80_percent_no_alert(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 1_000_000)
    _setup_telegram(db_session)

    with patch("app.notify.telegram.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True)
        check_and_send_budget_alerts(db_session)
        assert not mock_post.called


def test_crossing_80_percent_sends_alert(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 4_500_000)
    _setup_telegram(db_session)

    with patch("app.notify.telegram.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True)
        check_and_send_budget_alerts(db_session)
        assert mock_post.call_count == 1
        text = mock_post.call_args[1]["json"]["text"]
        assert "Budget Alert" in text
        assert "Approaching budget limit" in text


def test_re_run_does_not_duplicate_alert(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 4_500_000)
    _setup_telegram(db_session)

    with patch("app.notify.telegram.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True)
        check_and_send_budget_alerts(db_session)
        assert mock_post.call_count == 1

    with patch("app.notify.telegram.requests.post") as mock_post2:
        mock_post2.return_value = MagicMock(ok=True)
        check_and_send_budget_alerts(db_session)
        assert not mock_post2.called


def test_crossing_100_percent_sends_second_alert(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 4_500_000)
    _setup_telegram(db_session)

    with patch("app.notify.telegram.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True)
        check_and_send_budget_alerts(db_session)
        assert mock_post.call_count == 1

    _add_expense(db_session, food_cat.id, date.today(), 1_000_000)

    with patch("app.notify.telegram.requests.post") as mock_post2:
        mock_post2.return_value = MagicMock(ok=True)
        check_and_send_budget_alerts(db_session)
        assert mock_post2.call_count == 1
        text = mock_post2.call_args[1]["json"]["text"]
        assert "Over budget!" in text


def test_disabled_setting_no_alerts(db_session, food_cat):
    ym = _current_ym()
    _add_alloc(db_session, food_cat.id, ym, 5_000_000)
    _add_expense(db_session, food_cat.id, date.today(), 4_500_000)
    set_setting(db_session, "telegram_bot_token", "tok")
    set_setting(db_session, "telegram_chat_id", "123")
    set_setting(db_session, "telegram_budget_alerts_enabled", "false")

    with patch("app.notify.telegram.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True)
        check_and_send_budget_alerts(db_session)
        assert not mock_post.called


def test_unbudgeted_category_never_alerts(db_session, food_cat):
    _add_expense(db_session, food_cat.id, date.today(), 10_000_000)
    _setup_telegram(db_session)

    with patch("app.notify.telegram.requests.post") as mock_post:
        mock_post.return_value = MagicMock(ok=True)
        check_and_send_budget_alerts(db_session)
        assert not mock_post.called
