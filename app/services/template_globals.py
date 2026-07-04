"""Helpers exposed to Jinja templates via `env.globals` and context processors.

Centralized here so any router's Jinja2Templates instance can register them
without circular-import pain (the routers can't import from main.py).
"""

from __future__ import annotations

import threading
import time

from app.models.database import Category, SessionLocal

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


def register_template_globals(env) -> None:
    """Register all Jinja globals on a Starlette `Jinja2Templates` env."""
    env.globals["get_all_active_categories"] = get_all_active_categories
