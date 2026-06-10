"""Household profile resolution (Netflix-style picker, no passwords).

The selected profile is remembered with a plain `carange_profile=<user_id>`
cookie. Tailscale is the security boundary — the cookie only personalizes UI
preferences, it does not protect data.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from app.models.database import SessionLocal, User
from app.services.dashboard_layout import get_user_nav_items, get_user_sections

PROFILE_COOKIE = "carange_profile"
PROFILE_COOKIE_MAX_AGE = 365 * 24 * 3600

# Avatar chip palette offered on the picker page
PROFILE_COLORS = ("#2563EB", "#059669", "#D97706", "#DC2626", "#7C3AED", "#DB2777")
DEFAULT_PROFILE_COLOR = PROFILE_COLORS[0]


@dataclass
class ProfileContext:
    user: User
    visible_nav_items: frozenset
    visible_sections: frozenset


def _real_resolve_request_context(request: Request) -> ProfileContext | None:
    """Resolve the profile cookie into a ProfileContext, or None when the
    cookie is missing or points at a deleted profile."""
    raw = request.cookies.get(PROFILE_COOKIE)
    if not raw or not raw.isdigit():
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == int(raw)).first()
        if user is None:
            return None
        db.expunge(user)  # detach so templates can read attributes after close
        return ProfileContext(
            user=user,
            visible_nav_items=get_user_nav_items(db, user.id),
            visible_sections=get_user_sections(db, user.id),
        )
    finally:
        db.close()


# Patchable alias — the middleware and tests go through this name, so a single
# monkeypatch in conftest.py can stub out profile resolution app-wide.
resolve_request_context = _real_resolve_request_context


def touch_last_seen(db, user: User) -> None:
    user.last_seen_at = datetime.now(timezone.utc)
    db.commit()


def get_current_user(request: Request) -> User:
    """Dependency for handlers that need the resolved profile object."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="No profile selected")
    return user


def safe_next_path(raw: str | None) -> str:
    """Only allow same-origin absolute paths as post-select redirect targets."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"
