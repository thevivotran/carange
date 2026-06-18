"""Tests for the redesigned Telegram card (Task 05)."""

from unittest.mock import patch

from app.models.database import NotificationEvent
from app.notify.telegram import (
    _build_card_text,
    _budget_bar_line,
    inline_url_keyboard,
    send_transaction_ping_fields,
)
from notify_worker.worker import _build_message


def _base_fields(**overrides):
    base = {
        "tx_id": 42,
        "amount": 50_000,
        "tx_type": "expense",
        "cat_name": "Food",
        "description": "Lunch",
        "source": "manual",
        "needs_review": False,
        "bot_token": "tok",
        "chat_id": "123",
        "app_url": "https://app.example.com",
        "telegram_hide_amounts": "false",
    }
    base.update(overrides)
    return base


def _snapshot(**overrides):
    base = {
        "category_id": 1,
        "category_name": "Food",
        "category_color": "#EF4444",
        "allocated": 5_000_000,
        "spent": 3_000_000,
        "left": 2_000_000,
        "usage_pct": 60.0,
        "status": "On track",
        "available_balance": 2_000_000,
        "days_elapsed_pct": 50.0,
        "pace_status": "On pace",
    }
    base.update(overrides)
    return base


def test_inline_url_keyboard_basic():
    markup = inline_url_keyboard(
        "https://app.example.com",
        [
            ("View budget", "/budget"),
            ("Edit", "/transactions?focus=42"),
        ],
    )
    assert markup is not None
    rows = markup["inline_keyboard"]
    assert len(rows) == 1
    assert len(rows[0]) == 2
    assert rows[0][0]["url"] == "https://app.example.com/budget"
    assert rows[0][1]["url"] == "https://app.example.com/transactions?focus=42"


def test_inline_url_keyboard_rows_of_two():
    markup = inline_url_keyboard(
        "https://app.example.com",
        [
            ("A", "/a"),
            ("B", "/b"),
            ("C", "/c"),
        ],
    )
    rows = markup["inline_keyboard"]
    assert len(rows) == 2
    assert len(rows[0]) == 2
    assert len(rows[1]) == 1


def test_inline_url_keyboard_empty_app_url_returns_none():
    assert inline_url_keyboard("", [("X", "/x")]) is None
    assert inline_url_keyboard(None, [("X", "/x")]) is None


def test_budget_bar_line_renders_bar_and_status():
    snap = _snapshot(usage_pct=60.0, status="On track")
    line = _budget_bar_line(snap)
    assert "█" in line
    assert "░" in line
    assert "60%" in line
    assert "On track" in line


def test_budget_bar_line_over_100_gets_warning():
    snap = _snapshot(usage_pct=110.0, status="Over")
    line = _budget_bar_line(snap)
    assert "⚠️" in line


def test_build_card_text_with_snapshot():
    snap = _snapshot()
    text = _build_card_text("Header", ["body line"], "-50,000đ — Food", snap)
    assert "<b>Header</b>" in text
    assert "━━━━━━━━━━━━━━━━━━━━" in text
    assert "body line" in text
    assert "█" in text
    assert "On track" in text


def test_build_card_text_without_snapshot():
    text = _build_card_text("Header", ["body line"], "-50,000đ — Food", None)
    assert "█" not in text
    assert "<b>Header</b>" in text


def test_send_transaction_ping_fields_with_snapshot():
    snap = _snapshot(usage_pct=60.0, status="On track")
    fields = _base_fields(budget_snapshot=snap)

    with patch("app.notify.telegram._fire") as mock_fire:
        send_transaction_ping_fields(fields)
        mock_fire.assert_called_once()
        text = mock_fire.call_args[0][0]
        reply_markup = mock_fire.call_args.kwargs.get("reply_markup")

        assert "█" in text
        assert "░" in text
        assert "On track" in text
        assert reply_markup is not None
        urls = [btn["url"] for row in reply_markup["inline_keyboard"] for btn in row]
        assert any("/budget" in u for u in urls)


def test_send_transaction_ping_fields_without_snapshot():
    fields = _base_fields()

    with patch("app.notify.telegram._fire") as mock_fire:
        send_transaction_ping_fields(fields)
        mock_fire.assert_called_once()
        text = mock_fire.call_args[0][0]
        reply_markup = mock_fire.call_args.kwargs.get("reply_markup")

        assert "█" not in text
        assert "░" not in text
        assert reply_markup is not None
        urls = [btn["url"] for row in reply_markup["inline_keyboard"] for btn in row]
        assert any("/budget" in u for u in urls)


def test_send_transaction_ping_fields_preserves_spoiler():
    fields = _base_fields(telegram_hide_amounts="true")

    with patch("app.notify.telegram._fire") as mock_fire:
        send_transaction_ping_fields(fields)
        text = mock_fire.call_args[0][0]
        assert "<tg-spoiler>" in text


def test_build_message_budget_alert_has_bar_and_keyboard():
    evt = NotificationEvent(
        event_type="budget_alert",
        payload={
            "category_name": "Food",
            "spent": 4_500_000,
            "limit": 5_000_000,
            "pct": 90,
            "threshold": 80,
        },
    )
    cfg = {"app_url": "https://app.example.com", "telegram_hide_amounts": "false"}

    text, markup = _build_message(evt, cfg)
    assert text is not None
    assert "█" in text
    assert "░" in text
    assert "90%" in text
    assert markup is not None
    urls = [btn["url"] for row in markup["inline_keyboard"] for btn in row]
    assert any("/budget" in u for u in urls)


def test_build_message_budget_alert_over_100():
    evt = NotificationEvent(
        event_type="budget_alert",
        payload={
            "category_name": "Food",
            "spent": 6_000_000,
            "limit": 5_000_000,
            "pct": 120,
            "threshold": 100,
        },
    )
    cfg = {"app_url": "https://app.example.com", "telegram_hide_amounts": "false"}

    text, markup = _build_message(evt, cfg)
    assert "Over budget!" in text
    assert "█" in text
