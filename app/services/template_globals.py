"""Helpers exposed to Jinja templates via `env.globals` and context processors.

Centralized here so any router's Jinja2Templates instance can register them
without circular-import pain (the routers can't import from main.py).
"""

from __future__ import annotations

from app.models.database import Category, SessionLocal


def get_all_active_categories() -> list[Category]:
    """Return all active categories, ordered by name.

    Used by base.html's globally-included transaction modal to pre-populate
    the category <select> on first paint. Hits the DB on each call (called
    once per request); for higher-traffic pages consider caching at the
    dashboard-cache-invalidation layer.
    """
    session = SessionLocal()
    try:
        return session.query(Category).filter(Category.is_active.is_(True)).order_by(Category.name).all()
    finally:
        session.close()


def register_template_globals(env) -> None:
    """Register all Jinja globals on a Starlette `Jinja2Templates` env."""
    env.globals["get_all_active_categories"] = get_all_active_categories
