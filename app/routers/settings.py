import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models.database import get_db, SavingsBundle, SavingsStatus
from app.routers.fragments._helpers import render_fragment
from app.services.currency_format import CURRENCIES, DEFAULT_CURRENCY, inject_currency
from app.services.currency_format import register as register_currency_filters
from app.services.dashboard_layout import (
    NAV_PRESET_DESCRIPTIONS,
    NAV_PRESET_LABELS,
    NAV_PRESETS,
    PRESET_DESCRIPTIONS,
    PRESET_LABELS,
    PRESETS,
    get_dashboard_preset,
    get_nav_preset,
    inject_nav_items,
    set_dashboard_preset,
    set_nav_preset,
)
from app.services.sample_data_service import has_sample_data, load_sample_data, remove_sample_data
from app.services.settings_service import get_settings_bulk, set_setting

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

register_currency_filters(templates.env)
templates.context_processors.append(inject_currency)
templates.context_processors.append(inject_nav_items)


def _get_all_settings(db: Session) -> dict:
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
        },
    )

    nav_preset = get_nav_preset(db)

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
        "dashboard_preset": get_dashboard_preset(db),
        "dashboard_presets": [
            {"key": key, "label": PRESET_LABELS[key], "description": PRESET_DESCRIPTIONS[key]} for key in PRESETS
        ],
        "nav_preset": nav_preset,
        "nav_presets": [
            {"key": key, "label": NAV_PRESET_LABELS[key], "description": NAV_PRESET_DESCRIPTIONS[key]}
            for key in NAV_PRESETS
        ],
        "sample_data_loaded": has_sample_data(db),
        # masks: show placeholder bullet if a secret is already set
        "imap_password_set": bool(email["imap_password"]),
        "telegram_bot_token_set": bool(telegram["telegram_bot_token"]),
    }


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "settings/settings.html",
        {"active_menu": "settings", **_get_all_settings(db)},
    )


@router.post("/general")
async def save_general(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    currency = str(form.get("display_currency", "")).strip().upper()
    if currency in CURRENCIES:
        set_setting(db, "display_currency", currency)
    return render_fragment(request, "settings/_saved.html", {}, toast="General settings saved")


@router.post("/dashboard-goals")
async def save_dashboard_goals(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for key in ("savings_target_pct", "fi_target_vnd", "baby_fund_bundle_id"):
        if key in form:
            set_setting(db, key, str(form[key]).strip())
    return render_fragment(request, "settings/_saved.html", {}, toast="Dashboard goals saved")


@router.post("/dashboard")
async def save_dashboard(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    preset = str(form.get("dashboard_layout", "")).strip()
    if preset in PRESETS:
        set_dashboard_preset(db, preset)
    return render_fragment(request, "settings/_saved.html", {}, toast="Dashboard layout saved")


@router.post("/navigation")
async def save_navigation(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    preset = str(form.get("nav_layout", "")).strip()
    if preset in NAV_PRESETS:
        set_nav_preset(db, preset)
    return render_fragment(request, "settings/_saved.html", {}, toast="Navigation menu saved")


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
