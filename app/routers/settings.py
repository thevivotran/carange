import os
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models.database import get_db, SavingsBundle, SavingsStatus, Category, TransactionType
from app.routers.fragments._helpers import render_fragment
from app.services.currency_format import CURRENCIES, DEFAULT_CURRENCY, inject_currency
from app.services.currency_format import register as register_currency_filters
from app.services.dashboard_layout import (
    NAV_ITEM_DESCRIPTIONS,
    NAV_ITEM_LABELS,
    NAV_PRESET_DESCRIPTIONS,
    NAV_PRESET_LABELS,
    NAV_PRESETS,
    PRESET_DESCRIPTIONS,
    PRESET_LABELS,
    PRESETS,
    SECTION_DESCRIPTIONS,
    SECTION_LABELS,
    TOGGLEABLE_NAV_ITEMS,
    TOGGLEABLE_SECTIONS,
    apply_dashboard_preset,
    apply_nav_preset,
    get_user_nav_items,
    get_user_sections,
    inject_nav_items,
    match_dashboard_preset,
    match_nav_preset,
    set_user_nav_items,
    set_user_sections,
)
from app.services.sample_data_service import has_sample_data, load_sample_data, remove_sample_data
from app.services.settings_service import get_settings_bulk, set_setting

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

register_currency_filters(templates.env)
templates.context_processors.append(inject_currency)
templates.context_processors.append(inject_nav_items)


def _layout_context(db: Session, user_id: int) -> dict:
    """Per-profile dashboard/nav toggle state for the two layout cards."""
    enabled_sections = get_user_sections(db, user_id)
    enabled_nav = get_user_nav_items(db, user_id)
    return {
        "dashboard_preset": match_dashboard_preset(enabled_sections) or "custom",
        "dashboard_presets": [
            {"key": key, "label": PRESET_LABELS[key], "description": PRESET_DESCRIPTIONS[key]} for key in PRESETS
        ],
        "dashboard_sections": [
            {
                "key": key,
                "label": SECTION_LABELS[key],
                "description": SECTION_DESCRIPTIONS[key],
                "enabled": key in enabled_sections,
            }
            for key in TOGGLEABLE_SECTIONS
        ],
        "nav_preset": match_nav_preset(enabled_nav) or "custom",
        "nav_presets": [
            {"key": key, "label": NAV_PRESET_LABELS[key], "description": NAV_PRESET_DESCRIPTIONS[key]}
            for key in NAV_PRESETS
        ],
        "nav_items": [
            {
                "key": key,
                "label": NAV_ITEM_LABELS[key],
                "description": NAV_ITEM_DESCRIPTIONS[key],
                "enabled": key in enabled_nav,
            }
            for key in TOGGLEABLE_NAV_ITEMS
        ],
    }


def _get_all_settings(db: Session, user_id: int) -> dict:
    savings_bundles = (
        db.query(SavingsBundle.id, SavingsBundle.name)
        .filter(SavingsBundle.status == SavingsStatus.ACTIVE, SavingsBundle.deleted_at.is_(None))
        .order_by(SavingsBundle.name)
        .all()
    )

    general = get_settings_bulk(
        db,
        {
            "savings_target_pct": "25",
            "fi_target_vnd": "",
            "baby_fund_bundle_id": "",
            "display_currency": DEFAULT_CURRENCY,
            "month_start_day": "1",
            "forecast_buffer": "0",
        },
    )

    email = get_settings_bulk(
        db,
        {
            "imap_host": os.getenv("IMAP_HOST", "imap.gmail.com"),
            "imap_user": os.getenv("IMAP_USER", ""),
            "imap_password": os.getenv("IMAP_PASSWORD", ""),
            "imap_folder": os.getenv("IMAP_FOLDER", "INBOX"),
            "email_poll_interval": os.getenv("POLL_INTERVAL", "300"),
        },
    )

    telegram = get_settings_bulk(
        db,
        {
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        },
    )

    ocr = get_settings_bulk(
        db,
        {
            "ollama_url": os.getenv("OLLAMA_URL", ""),
            "ollama_model": os.getenv("OLLAMA_MODEL", "Qwen3.6-35B-A3B"),
        },
    )

    thresholds = get_settings_bulk(
        db,
        {
            "review_threshold": os.getenv("REVIEW_THRESHOLD", "0.95"),
            "anomaly_multiplier": os.getenv("ANOMALY_MULTIPLIER", "3.0"),
            "anomaly_min_samples": os.getenv("ANOMALY_MIN_SAMPLES", "3"),
            "stuck_timeout_min": os.getenv("STUCK_TIMEOUT_MIN", "30"),
            "max_retries": os.getenv("MAX_EMAIL_RETRIES", "3"),
        },
    )

    return {
        **general,
        **email,
        **telegram,
        **ocr,
        **thresholds,
        "savings_bundles": [{"id": r.id, "name": r.name} for r in savings_bundles],
        "currencies": [{"code": code, "label": cfg["label"]} for code, cfg in CURRENCIES.items()],
        **_layout_context(db, user_id),
        "sample_data_loaded": has_sample_data(db),
        # masks: show placeholder bullet if a secret is already set
        "imap_password_set": bool(email["imap_password"]),
        "telegram_bot_token_set": bool(telegram["telegram_bot_token"]),
        # KPI category assignments
        "kpi_expense_categories": [
            {"id": c.id, "name": c.name, "kpi_role": c.kpi_role or ""}
            for c in db.query(Category)
            .filter(Category.type == TransactionType.EXPENSE, Category.is_active.isnot(False))
            .order_by(Category.name)
            .all()
        ],
    }


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    from app.services.fiscal_period import get_month_start_day, suggest_salary_day

    context = {"active_menu": "settings", **_get_all_settings(db, request.state.user.id)}

    month_start_day = get_month_start_day(db)
    suggested_day = suggest_salary_day(db)
    if suggested_day == month_start_day:
        suggested_day = None
    context["month_start_day"] = month_start_day
    context["suggested_day"] = suggested_day
    if suggested_day:
        context["suggested_day_label"] = f"{suggested_day}{_ordinal_suffix(suggested_day)}"

    return templates.TemplateResponse(request, "settings/settings.html", context)


@router.post("/general")
async def save_general(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    currency = str(form.get("display_currency", "")).strip().upper()
    if currency in CURRENCIES:
        set_setting(db, "display_currency", currency)
    return render_fragment(request, "settings/_saved.html", {}, toast="General settings saved")


@router.post("/forecast-buffer")
async def save_forecast_buffer(request: Request, db: Session = Depends(get_db)):
    from app.services.dashboard_service import invalidate_dashboard_cache

    form = await request.form()
    try:
        value = float(str(form.get("forecast_buffer", "")).strip())
    except ValueError:
        raise HTTPException(400, "forecast_buffer must be a number")
    if value < 0:
        raise HTTPException(400, "forecast_buffer must be non-negative")

    if value == int(value):
        value_str = str(int(value))
    else:
        value_str = str(value)

    set_setting(db, "forecast_buffer", value_str)
    invalidate_dashboard_cache(db)
    return render_fragment(request, "settings/_saved.html", {}, toast="Cash buffer saved")


@router.post("/dashboard-goals")
async def save_dashboard_goals(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for key in ("savings_target_pct", "fi_target_vnd", "baby_fund_bundle_id"):
        if key in form:
            set_setting(db, key, str(form[key]).strip())
    return render_fragment(request, "settings/_saved.html", {}, toast="Dashboard goals saved")


def _ordinal_suffix(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _last_day_of_prev_month(today: date) -> int:
    import calendar

    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return calendar.monthrange(y, m)[1]


@router.post("/pay-cycle")
async def save_pay_cycle(request: Request, db: Session = Depends(get_db)):
    from app.services.dashboard_service import invalidate_dashboard_cache
    from app.services.fiscal_period import (
        MAX_DAY,
        MIN_DAY,
        current_period_label,
        fiscal_window,
        get_month_start_day,
    )

    old_day = get_month_start_day(db)

    form = await request.form()
    try:
        day = int(str(form.get("month_start_day", "1")).strip())
    except ValueError:
        day = MIN_DAY
    day = max(MIN_DAY, min(MAX_DAY, day))
    set_setting(db, "month_start_day", str(day))
    invalidate_dashboard_cache(db)

    changed = day != old_day
    context = {"changed": changed}
    if changed:
        today = date.today()
        label = current_period_label(today, day)
        start, end = fiscal_window(label, day)
        prev_day = day - 1 if day > 1 else _last_day_of_prev_month(today)
        context.update(
            day=day,
            day_suffix=_ordinal_suffix(day),
            prev_day=prev_day,
            prev_day_suffix=_ordinal_suffix(prev_day),
            window_start=start.strftime("%b %d"),
            window_end=end.strftime("%b %d, %Y"),
        )

    return render_fragment(
        request,
        "settings/_pay_cycle_saved.html",
        context,
        toast="Pay cycle saved",
    )


@router.post("/dashboard")
async def save_dashboard(request: Request, db: Session = Depends(get_db)):
    """Per-profile dashboard sections: a preset quick-apply button submits
    `preset=<key>`; the Save button submits the `sections` checkbox list."""
    form = await request.form()
    user_id = request.state.user.id
    preset = str(form.get("preset", "")).strip()
    if preset in PRESETS:
        apply_dashboard_preset(db, user_id, preset)
        toast = f"Dashboard layout set to {PRESET_LABELS[preset]}"
    else:
        set_user_sections(db, user_id, form.getlist("sections"))
        toast = "Dashboard layout saved"
    return render_fragment(request, "settings/_dashboard_layout_card.html", _layout_context(db, user_id), toast=toast)


@router.post("/navigation")
async def save_navigation(request: Request, db: Session = Depends(get_db)):
    """Per-profile nav items: preset quick-apply or `nav_items` checkbox list."""
    form = await request.form()
    user_id = request.state.user.id
    preset = str(form.get("preset", "")).strip()
    if preset in NAV_PRESETS:
        apply_nav_preset(db, user_id, preset)
        toast = f"Navigation menu set to {NAV_PRESET_LABELS[preset]}"
    else:
        set_user_nav_items(db, user_id, form.getlist("nav_items"))
        toast = "Navigation menu saved"
    return render_fragment(request, "settings/_navigation_card.html", _layout_context(db, user_id), toast=toast)


@router.post("/kpi-terms")
async def save_kpi_terms(request: Request, db: Session = Depends(get_db)):
    """Household-wide: assign KPI role to expense categories."""
    from app.services.dashboard_service import invalidate_dashboard_cache, VALID_KPI_ROLES

    form = await request.form()
    categories = (
        db.query(Category)
        .filter(Category.type == TransactionType.EXPENSE, Category.is_active.isnot(False))
        .order_by(Category.name)
        .all()
    )
    for cat in categories:
        raw = form.get(f"role_{cat.id}", "").strip()
        cat.kpi_role = raw if raw in VALID_KPI_ROLES else None
    db.commit()
    invalidate_dashboard_cache(db)
    return render_fragment(
        request,
        "settings/_kpi_terms_card.html",
        {
            "kpi_expense_categories": [{"id": c.id, "name": c.name, "kpi_role": c.kpi_role or ""} for c in categories],
        },
        toast="KPI categories updated",
    )


@router.post("/sample-data/load")
def load_sample_data_route(request: Request, db: Session = Depends(get_db)):
    count = load_sample_data(db)
    toast = (
        f"Loaded {count} sample records — explore the dashboard, then remove them anytime from here."
        if count
        else "Sample data is already loaded."
    )
    return render_fragment(request, "settings/_sample_data_card.html", {"sample_data_loaded": True}, toast=toast)


@router.post("/sample-data/remove")
def remove_sample_data_route(request: Request, db: Session = Depends(get_db)):
    removed = remove_sample_data(db)
    toast = f"Removed {removed} sample records." if removed else "No sample data to remove."
    return render_fragment(request, "settings/_sample_data_card.html", {"sample_data_loaded": False}, toast=toast)


@router.post("/email")
async def save_email(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    keys = ("imap_host", "imap_user", "imap_folder", "email_poll_interval")
    for key in keys:
        if key in form:
            set_setting(db, key, str(form[key]).strip())
    # only overwrite password if user typed something new
    new_pw = str(form.get("imap_password", "")).strip()
    if new_pw:
        set_setting(db, "imap_password", new_pw)
    return render_fragment(request, "settings/_saved.html", {}, toast="Email settings saved")


@router.post("/telegram")
async def save_telegram(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    chat_id = str(form.get("telegram_chat_id", "")).strip()
    if chat_id:
        set_setting(db, "telegram_chat_id", chat_id)
    new_token = str(form.get("telegram_bot_token", "")).strip()
    if new_token:
        set_setting(db, "telegram_bot_token", new_token)
    return render_fragment(request, "settings/_saved.html", {}, toast="Telegram settings saved")


@router.post("/ocr")
async def save_ocr(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for key in ("ollama_url", "ollama_model"):
        if key in form:
            set_setting(db, key, str(form[key]).strip())
    return render_fragment(request, "settings/_saved.html", {}, toast="OCR/AI settings saved")


@router.post("/thresholds")
async def save_thresholds(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    keys = ("review_threshold", "anomaly_multiplier", "anomaly_min_samples", "stuck_timeout_min", "max_retries")
    for key in keys:
        if key in form:
            set_setting(db, key, str(form[key]).strip())
    return render_fragment(request, "settings/_saved.html", {}, toast="Threshold settings saved")
