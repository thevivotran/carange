import json

from sqlalchemy.orm import Session

from app.services.settings_service import get_setting, get_user_setting, set_setting, set_user_setting

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

# Canonical, ordered list of dashboard sections a profile can toggle. Must stay
# in sync with the {% if '<key>' in visible_sections %} blocks in dashboard.html
# and partials/dashboard/_kpi_cards.html.
TOGGLEABLE_SECTIONS: tuple[str, ...] = (
    "kpi_extra",
    "cash_flow",
    "budget_snapshot",
    "active_projects",
    "savings_goals",
    "wealth_building",
    "stress_test",
)

SECTION_LABELS = {
    "kpi_extra": "Extra KPI cards",
    "cash_flow": "Cash Flow chart",
    "budget_snapshot": "Budget Snapshot",
    "active_projects": "Active Projects",
    "savings_goals": "Savings Goals",
    "wealth_building": "Wealth Building Analysis",
    "stress_test": "One-Income Stress Test",
}

SECTION_DESCRIPTIONS = {
    "kpi_extra": "Real Estate Rate, Emergency Fund, FI Progress, and other secondary cards.",
    "cash_flow": "Income vs expense bar chart for the last 6 months.",
    "budget_snapshot": "Top overspending categories at a glance.",
    "active_projects": "Progress cards for in-flight financial projects.",
    "savings_goals": "Progress cards for active savings goals.",
    "wealth_building": "Collapsible deep-dive into wealth-building spend.",
    "stress_test": "What-if analysis of living on a single income.",
}


def get_dashboard_preset(db: Session) -> str:
    value = get_setting(db, "dashboard_layout", DEFAULT_PRESET)
    return value if value in PRESETS else DEFAULT_PRESET


def get_visible_sections(db: Session) -> frozenset[str]:
    """Household-default sections — used to seed new profiles and as the
    fallback for profiles that have never saved their own preference."""
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

# Canonical, ordered list of nav items a profile can toggle. Must stay in sync
# with the {% if '<key>' in visible_nav_items %} blocks in base.html.
TOGGLEABLE_NAV_ITEMS: tuple[str, ...] = ("import", "pulse", "review", "projects", "assets", "notes")

NAV_ITEM_LABELS = {
    "import": "Import",
    "pulse": "Daily Pulse",
    "review": "Review Inbox",
    "projects": "Projects",
    "assets": "Assets",
    "notes": "IOUs & Notes",
}

NAV_ITEM_DESCRIPTIONS = {
    "import": "Upload bank screenshots for OCR import.",
    "pulse": "Single-screen daily check-in on monthly health.",
    "review": "Inbox of auto-imported transactions awaiting approval.",
    "projects": "Financial projects with payment schedules.",
    "assets": "Gold, currency, and other non-cash holdings.",
    "notes": "IOUs and freeform money notes.",
}


def get_nav_preset(db: Session) -> str:
    value = get_setting(db, "nav_layout", DEFAULT_NAV_PRESET)
    return value if value in NAV_PRESETS else DEFAULT_NAV_PRESET


def set_nav_preset(db: Session, preset: str) -> None:
    if preset not in NAV_PRESETS:
        raise ValueError(f"Unknown nav preset: {preset}")
    set_setting(db, "nav_layout", preset)


def get_visible_nav_items(db: Session) -> frozenset[str]:
    """Household-default nav items — used to seed new profiles and as the
    fallback for profiles that have never saved their own preference."""
    return NAV_CORE | NAV_PRESETS[get_nav_preset(db)]


# ── Per-profile preferences ───────────────────────────────────────────────────
# Stored in user_settings as JSON arrays of toggleable keys only; NAV_CORE and
# the always-on dashboard anchors are unioned/implied at read time.


def _parse_toggles(raw: str, allowed: tuple[str, ...]) -> frozenset[str]:
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return frozenset()
    if not isinstance(items, list):
        return frozenset()
    return frozenset(i for i in items if i in allowed)


def get_user_nav_items(db: Session, user_id: int) -> frozenset[str]:
    raw = get_user_setting(db, user_id, "nav_items")
    if raw is None:
        return get_visible_nav_items(db)
    return NAV_CORE | _parse_toggles(raw, TOGGLEABLE_NAV_ITEMS)


def set_user_nav_items(db: Session, user_id: int, items) -> None:
    toggles = sorted(frozenset(items) & frozenset(TOGGLEABLE_NAV_ITEMS))
    set_user_setting(db, user_id, "nav_items", json.dumps(toggles))


def get_user_sections(db: Session, user_id: int) -> frozenset[str]:
    raw = get_user_setting(db, user_id, "dashboard_sections")
    if raw is None:
        return get_visible_sections(db)
    return _parse_toggles(raw, TOGGLEABLE_SECTIONS)


def set_user_sections(db: Session, user_id: int, sections) -> None:
    toggles = sorted(frozenset(sections) & frozenset(TOGGLEABLE_SECTIONS))
    set_user_setting(db, user_id, "dashboard_sections", json.dumps(toggles))


def apply_nav_preset(db: Session, user_id: int, preset: str) -> None:
    if preset not in NAV_PRESETS:
        raise ValueError(f"Unknown nav preset: {preset}")
    set_user_nav_items(db, user_id, NAV_PRESETS[preset])


def apply_dashboard_preset(db: Session, user_id: int, preset: str) -> None:
    if preset not in PRESETS:
        raise ValueError(f"Unknown dashboard preset: {preset}")
    set_user_sections(db, user_id, PRESETS[preset])


def seed_user_prefs_from_globals(db: Session, user_id: int) -> None:
    """New profiles start from the household defaults so the UI looks the same
    before and after the profile picker was introduced."""
    set_user_nav_items(db, user_id, get_visible_nav_items(db) - NAV_CORE)
    set_user_sections(db, user_id, get_visible_sections(db))


def match_nav_preset(items: frozenset[str]) -> str | None:
    """Preset key whose toggle set equals the profile's, or None for custom."""
    toggles = items - NAV_CORE
    for key, preset_items in NAV_PRESETS.items():
        if toggles == preset_items:
            return key
    return None


def match_dashboard_preset(sections: frozenset[str]) -> str | None:
    for key, preset_sections in PRESETS.items():
        if frozenset(sections) == preset_sections:
            return key
    return None


def inject_nav_items(request) -> dict:
    """Context processor: expose the resolved profile and its visible nav set
    (placed on request.state by ProfileMiddleware) to every template."""
    return {
        "visible_nav_items": getattr(request.state, "visible_nav_items", NAV_CORE),
        "current_user": getattr(request.state, "user", None),
    }
