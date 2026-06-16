import logging

from sqlalchemy.orm import Session

from app.models.database import NotificationEvent

log = logging.getLogger("app.services.notification_service")


def publish_notification(db: Session, event_type: str, payload: dict) -> None:
    """Insert a pending notification event. pg_notify fires when the caller commits."""
    evt = NotificationEvent(event_type=event_type, payload=payload)
    db.add(evt)
    db.flush()
