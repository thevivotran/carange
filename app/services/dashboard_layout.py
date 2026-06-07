from sqlalchemy.orm import Session

from app.models.database import SessionLocal
from app.services.settings_service import get_setting, set_setting

DEFAULT_PRESET = "full"
DEFAULT_NAV_PRESET = "full"

# Section keys that can be toggled on/off per preset. Anything not listed here
# (KPI core row, Net Worth strip, Safety Score, Alerts, Recent Transactions, …)
# always renders — those are the "can't be disabled" anchors every user needs.
PRESETS: dict[str, frozenset[str]] = {
    "simple": frozenset(),
    "standard": frozenset({"kpi_extra", "cash_flow", "budget_snapshot", "active_projects", "savings_goals"}),
    "full": frozenset(
        {
            "kpi_extra",
            "cash_flow",
            "budget_snapshot",
            "active_projects",
            "savings_goals",
            "wealth_building",
            "stress_test",
        }
    ),
}

PRESET_LABELS = {
    "simple": "Simple",
    "standard": "Standard",
    "full": "Full",
}

PRESET_DESCRIPTIONS = {
    "simple": "Just the essentials — core health cards, net worth, and recent transactions.",
    "standard": "Adds cash flow, budget snapshot, projects, and savings goals.",
    "full": "Everything, including Wealth Building and One-Income Stress Test analysis.",
}


def get_dashboard_preset(db: Session) -> str:
    value = get_setting(db, "dashboard_layout", DEFAULT_PRESET)
    return value if value in PRESETS else DEFAULT_PRESET


def get_visible_sections(db: Session) -> frozenset[str]:
    return PRESETS[get_dashboard_preset(db)]


def set_dashboard_preset(db: Session, preset: str) -> None:
    if preset not in PRESETS:
        raise ValueError(f"Unknown dashboard preset: {preset}")
    set_setting(db, "dashboard_layout", preset)


# Sidebar/bottom-nav items every user needs regardless of preset — the
# day-to-day core of logging spend and checking the budget.
NAV_CORE = frozenset({"dashboard", "transactions", "budget", "savings", "settings"})

# Items layered on top of NAV_CORE per preset. Mirrors the same progressive-
# disclosure idea as PRESETS above: Simple shows only what a brand-new family
# needs in week one, Standard adds the next tier of "actionable" pages, and
# Full surfaces everything, including the more occasional-use pages.
#
# Deliberately a SEPARATE preset/setting from PRESETS (dashboard cards) — how
# much detail the dashboard shows and how many pages the nav exposes are two
# different concerns a user may want to tune independently (e.g. someone who
# wants a clutter-free dashboard may still want quick nav access to Projects).
NAV_PRESETS: dict[str, frozenset[str]] = {
    "simple": frozenset(),
    "standard": frozenset({"import", "pulse", "review", "projects"}),
    "full": frozenset({"import", "pulse", "review", "projects", "assets", "notes"}),
}

NAV_PRESET_LABELS = {
    "simple": "Simple",
    "standard": "Standard",
    "full": "Full",
}

NAV_PRESET_DESCRIPTIONS = {
    "simple": "Just the day-to-day core: Dashboard, Transactions, Budget, Savings, Settings.",
    "standard": "Adds Import, Daily Pulse, Review Inbox, and Projects.",
    "full": "Everything, including Assets and IOUs & Notes.",
}


def get_nav_preset(db: Session) -> str:
    value = get_setting(db, "nav_layout", DEFAULT_NAV_PRESET)
    return value if value in NAV_PRESETS else DEFAULT_NAV_PRESET


def set_nav_preset(db: Session, preset: str) -> None:
    if preset not in NAV_PRESETS:
        raise ValueError(f"Unknown nav preset: {preset}")
    set_setting(db, "nav_layout", preset)


def get_visible_nav_items(db: Session) -> frozenset[str]:
    return NAV_CORE | NAV_PRESETS[get_nav_preset(db)]


def inject_nav_items(request) -> dict:
    """Context processor: makes the visible nav item set available to every template."""
    db = SessionLocal()
    try:
        return {"visible_nav_items": get_visible_nav_items(db)}
    finally:
        db.close()
