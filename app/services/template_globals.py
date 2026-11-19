"""Helpers exposed to Jinja templates via `env.globals` and context processors.

Centralized here so any router's Jinja2Templates instance can register them
without circular-import pain (the routers can't import from main.py).
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from app.models.database import Category, SessionLocal, User

# Every full-page render (16+ templates extend base.html) calls this global,
# each previously opening its own SessionLocal() just to list active
# categories for the quick-add modal. Cache with a short TTL and invalidate
# explicitly on writes so category edits still show up promptly.
_CACHE_TTL = 60.0  # seconds
_cache: list[Category] | None = None
_cache_ts: float = 0.0
_cache_lock = threading.Lock()


def invalidate_active_categories_cache() -> None:
    """Call after any category create/update/delete/merge that could change the active list."""
    global _cache
    with _cache_lock:
        _cache = None


def get_all_active_categories() -> list[Category]:
    """Return all active categories, ordered by name.

    Used by base.html's globally-included transaction modal to pre-populate
    the category <select> on first paint. Cached in-process for _CACHE_TTL
    seconds to avoid a fresh DB session + query on every page render.
    """
    global _cache, _cache_ts
    with _cache_lock:
        if _cache is not None and time.monotonic() - _cache_ts <= _CACHE_TTL:
            return _cache

    session = SessionLocal()
    try:
        categories = session.query(Category).filter(Category.is_active.is_(True)).order_by(Category.name).all()
        for c in categories:
            session.expunge(c)
    finally:
        session.close()

    with _cache_lock:
        _cache = categories
        _cache_ts = time.monotonic()
        return _cache


def get_all_users() -> list[User]:
    """Return all household profiles, ordered by name.

    Used to populate the spender <select> in the transaction modal and list
    filters. The users table has only a handful of rows (one per household
    member), so no caching — a fresh query per render is cheap.
    """
    session = SessionLocal()
    try:
        users = session.query(User).order_by(User.name).all()
        for u in users:
            session.expunge(u)
        return users
    finally:
        session.close()


_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_static_hashes: dict[str, tuple[float, str]] = {}


def static_url(path: str) -> str:
    """`/static/<path>?v=<content hash>` so browsers and the service worker (which serves
    /static/ cache-first) pick up a changed file instead of a stale cached copy."""
    file = _STATIC_DIR / path
    try:
        mtime = file.stat().st_mtime
    except OSError:
        return f"/static/{path}"
    cached = _static_hashes.get(path)
    if cached is None or cached[0] != mtime:
        digest = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
        cached = (mtime, digest)
        _static_hashes[path] = cached
    return f"/static/{path}?v={cached[1]}"


def register_template_globals(env) -> None:
    """Register all Jinja globals on a Starlette `Jinja2Templates` env."""
    env.globals["get_all_active_categories"] = get_all_active_categories
    env.globals["get_all_users"] = get_all_users
    env.globals["static_url"] = static_url
    from app.services.date_format import register as register_date_filters

    register_date_filters(env)
