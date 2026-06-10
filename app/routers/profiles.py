from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models.database import User, UserSetting, get_db
from app.services.dashboard_layout import seed_user_prefs_from_globals
from app.services.profiles import (
    DEFAULT_PROFILE_COLOR,
    PROFILE_COOKIE,
    PROFILE_COOKIE_MAX_AGE,
    PROFILE_COLORS,
    safe_next_path,
    touch_last_seen,
)

router = APIRouter()

# Standalone template env — the picker renders before a profile exists, so it
# must not depend on the nav/currency context processors of the main env.
templates = Jinja2Templates(directory="app/templates")


def _render_picker(request: Request, db: Session, *, next_path: str = "/", error: str | None = None):
    users = db.query(User).order_by(User.created_at, User.id).all()
    current = getattr(request.state, "user", None)
    return templates.TemplateResponse(
        request,
        "profiles.html",
        {
            "users": users,
            "current_user_id": current.id if current else None,
            "colors": PROFILE_COLORS,
            "next_path": next_path,
            "error": error,
        },
    )


def _select_response(user: User, next_path: str) -> RedirectResponse:
    response = RedirectResponse(safe_next_path(next_path), status_code=303)
    response.set_cookie(
        PROFILE_COOKIE,
        str(user.id),
        max_age=PROFILE_COOKIE_MAX_AGE,
        samesite="lax",
        httponly=True,
    )
    return response


@router.get("", response_class=HTMLResponse)
def profiles_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    return _render_picker(request, db, next_path=safe_next_path(next))


@router.post("/select")
async def select_profile(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    raw_id = str(form.get("user_id", ""))
    next_path = safe_next_path(str(form.get("next", "/")))
    user = db.query(User).filter(User.id == int(raw_id)).first() if raw_id.isdigit() else None
    if user is None:
        return _render_picker(request, db, next_path=next_path, error="That profile no longer exists.")
    touch_last_seen(db, user)
    return _select_response(user, next_path)


@router.post("/create")
async def create_profile(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    color = str(form.get("color", DEFAULT_PROFILE_COLOR))
    if color not in PROFILE_COLORS:
        color = DEFAULT_PROFILE_COLOR
    next_path = safe_next_path(str(form.get("next", "/")))

    if not name or len(name) > 50:
        return _render_picker(request, db, next_path=next_path, error="Please enter a name (max 50 characters).")
    if db.query(User).filter(User.name == name).first():
        return _render_picker(request, db, next_path=next_path, error=f"A profile named “{name}” already exists.")

    user = User(name=name, color=color)
    db.add(user)
    db.commit()
    db.refresh(user)
    seed_user_prefs_from_globals(db, user.id)
    touch_last_seen(db, user)
    return _select_response(user, next_path)


@router.post("/{user_id}/delete")
def delete_profile(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        return _render_picker(request, db, error="That profile no longer exists.")
    if db.query(User).count() <= 1:
        return _render_picker(request, db, error="Cannot delete the last profile.")
    # Explicit child delete: SQLite only honors ON DELETE CASCADE with the FK
    # pragma enabled, so don't rely on it.
    db.query(UserSetting).filter(UserSetting.user_id == user_id).delete()
    db.delete(user)
    db.commit()

    response = RedirectResponse("/profiles", status_code=303)
    current = getattr(request.state, "user", None)
    if current is not None and current.id == user_id:
        response.delete_cookie(PROFILE_COOKIE)
    return response
