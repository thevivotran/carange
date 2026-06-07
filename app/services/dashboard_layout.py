from sqlalchemy.orm import Session

from app.services.settings_service import get_setting, set_setting

DEFAULT_PRESET = "full"

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
