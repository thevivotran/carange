from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.database import Setting


def get_setting(db: Session, key: str, default: Optional[str] = None) -> Optional[str]:
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        db.add(Setting(key=key, value=value, updated_at=datetime.now(timezone.utc)))
    db.commit()
