from unittest.mock import MagicMock

from app.models.database import NotificationEvent, NotificationEventStatus
from app.services.notification_service import publish_notification


def test_publish_notification_inserts_row(db_session):
    publish_notification(db_session, "advance_ping", {"tx_id": 1})
    db_session.commit()

    row = db_session.query(NotificationEvent).filter_by(event_type="advance_ping").one()
    assert row.status == NotificationEventStatus.PENDING
    assert row.payload == {"tx_id": 1}


def test_publish_notification_payload_preserved(db_session):
    payload = {"tx_id": 42, "amount": "1500000", "category": "food", "note": "lunch"}
    publish_notification(db_session, "tx_alert", payload)
    db_session.commit()

    row = db_session.query(NotificationEvent).filter_by(event_type="tx_alert").one()
    assert row.payload["tx_id"] == 42
    assert row.payload["amount"] == "1500000"
    assert row.payload["category"] == "food"
    assert row.payload["note"] == "lunch"


def test_publish_notification_flush_error_does_not_propagate_if_caller_catches(db_session):
    original_flush = db_session.flush
    db_session.flush = MagicMock(side_effect=Exception("db error"))

    caught = False
    try:
        publish_notification(db_session, "advance_ping", {"tx_id": 99})
    except Exception:
        caught = True
    finally:
        db_session.flush = original_flush

    assert caught
