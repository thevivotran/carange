"""Tests for notify_worker.worker module."""

from datetime import datetime, timedelta, timezone

from app.models.database import NotificationEvent, NotificationEventStatus
from notify_worker.worker import _claim_next, _handle_failure, _build_message, MAX_RETRIES


def test_claim_next_returns_pending_event(db_session):
    """Insert a pending event and verify it's claimed with status=PROCESSING."""
    evt = NotificationEvent(event_type="advance_ping", payload={})
    db_session.add(evt)
    db_session.commit()

    claimed = _claim_next(db_session)
    assert claimed is not None
    assert claimed.status == NotificationEventStatus.PROCESSING


def test_claim_next_skips_future_retry_after(db_session):
    """Events with retry_after in the future should not be claimed."""
    evt = NotificationEvent(
        event_type="advance_ping",
        payload={},
        retry_after=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add(evt)
    db_session.commit()

    claimed = _claim_next(db_session)
    assert claimed is None


def test_claim_next_returns_none_when_empty(db_session):
    """No events in the table should return None."""
    claimed = _claim_next(db_session)
    assert claimed is None


def test_handle_failure_schedules_retry(db_session):
    """First failure should schedule a retry with status=PENDING."""
    evt = NotificationEvent(
        event_type="advance_ping",
        payload={},
        status=NotificationEventStatus.PROCESSING,
        retry_count=0,
    )
    db_session.add(evt)
    db_session.commit()

    _handle_failure(db_session, evt, "boom")

    db_session.refresh(evt)
    assert evt.status == NotificationEventStatus.PENDING
    assert evt.retry_count == 1
    assert evt.retry_after is not None
    assert evt.retry_after > datetime.now(timezone.utc)


def test_handle_failure_permanent_after_max_retries(db_session):
    """After MAX_RETRIES, status should be FAILED."""
    evt = NotificationEvent(
        event_type="advance_ping",
        payload={},
        status=NotificationEventStatus.PROCESSING,
        retry_count=MAX_RETRIES,
    )
    db_session.add(evt)
    db_session.commit()

    _handle_failure(db_session, evt, "final failure")

    db_session.refresh(evt)
    assert evt.status == NotificationEventStatus.FAILED


def test_build_message_advance_ping_created():
    """Build message for advance_ping with action=created."""
    evt = NotificationEvent(
        event_type="advance_ping",
        payload={
            "amount": "1000000",
            "action": "created",
            "cat_name": "Food",
            "description": "Test advance",
        },
    )
    cfg = {"app_url": "https://example.com", "telegram_hide_amounts": "false"}

    text = _build_message(evt, cfg)
    assert text is not None
    assert "Personal advance — Created" in text


def test_build_message_review_reminder_zero():
    """Review reminder with count=0 should return None."""
    evt = NotificationEvent(
        event_type="review_reminder",
        payload={"count": 0},
    )
    cfg = {"app_url": "https://example.com"}

    text = _build_message(evt, cfg)
    assert text is None


def test_build_message_unknown_event_returns_none():
    """Unknown event_type should return None."""
    evt = NotificationEvent(
        event_type="unknown_event",
        payload={},
    )
    cfg = {"app_url": "https://example.com"}

    text = _build_message(evt, cfg)
    assert text is None
