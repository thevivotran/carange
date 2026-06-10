import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Setting, UserSetting


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def get_user_setting(db: Session, user_id: int, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.key == key).first()
    return row.value if row else default


def set_user_setting(db: Session, user_id: int, key: str, value: str) -> None:
    row = db.query(UserSetting).filter(UserSetting.user_id == user_id, UserSetting.key == key).first()
    now = datetime.now(timezone.utc)
    if row:
        row.value = value
        row.updated_at = now
    else:
        db.add(UserSetting(user_id=user_id, key=key, value=value, updated_at=now))
    db.commit()


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.add(Setting(key=key, value=value, updated_at=datetime.now(timezone.utc)))
    db.commit()

    if key == "display_currency":
        from app.services.currency_format import invalidate_cache

        invalidate_cache()


def get_settings_bulk(db: Session, keys_with_defaults: dict[str, str]) -> dict[str, str]:
    """Fetch multiple setting keys at once, returning defaults for missing ones."""
    rows = db.query(Setting).filter(Setting.key.in_(keys_with_defaults.keys())).all()
    result = dict(keys_with_defaults)
    for row in rows:
        result[row.key] = row.value
    return result


def get_email_config(db: Session) -> dict[str, str]:
    """Return email worker config, falling back to env vars for each key."""
    defaults = {
        "imap_host": os.getenv("IMAP_HOST", "imap.gmail.com"),
        "imap_user": os.getenv("IMAP_USER", ""),
        "imap_password": os.getenv("IMAP_PASSWORD", ""),
        "imap_folder": os.getenv("IMAP_FOLDER", "INBOX"),
        "email_poll_interval": os.getenv("POLL_INTERVAL", "300"),
    }
    return get_settings_bulk(db, defaults)
