"""Outbound Telegram notifications (Phase 1 — send-only).

Credentials and the optional app URL are resolved per-call via
`app.services.settings_service.get_telegram_config`, which checks the database
first (set via the Settings UI) and falls back to the TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID / APP_URL env vars. If bot token or chat ID are missing,
functions log a debug message and return False.
"""

import html
import logging
import threading

import requests
from sqlalchemy.orm import Session

from app.services.settings_service import get_telegram_config

log = logging.getLogger("app.notify.telegram")

_API_BASE = "https://api.telegram.org"
_TIMEOUT = 10


def _esc(text) -> str:
    return html.escape(str(text), quote=False)


def _amount(amount_str: str, hide: bool) -> str:
    if hide:
        return f"<tg-spoiler>{amount_str}</tg-spoiler>"
    return amount_str


def _budget_link(app_url: str, label: str = "Open Budget") -> str:
    if app_url:
        return f'📊 <a href="{app_url.rstrip("/")}/budget">{label}</a>'
    return f"📊 {label}."


def _send(text: str, bot_token: str, chat_id: str, parse_mode: str = "HTML") -> bool:
    """POST a message to the configured chat. Returns True on success (blocking)."""
    if not bot_token or not chat_id:
        log.debug("Telegram not configured — skipping notification")
        return False
    try:
        resp = requests.post(
            f"{_API_BASE}/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            log.warning("Telegram API error %d: %s", resp.status_code, resp.text[:200])
        return resp.ok
    except requests.RequestException as exc:
        log.warning("Telegram send failed: %s", exc)
        return False


def _fire(text: str, bot_token: str, chat_id: str, parse_mode: str = "HTML") -> None:
    """Non-blocking variant of _send — spawns a daemon thread so the caller returns immediately."""
    if not bot_token or not chat_id:
        log.debug("Telegram not configured — skipping notification")
        return
    threading.Thread(target=_send, args=(text, bot_token, chat_id, parse_mode), daemon=True).start()


def _review_link(app_url: str) -> str:
    """Return the 'needs review' line, linking to the filtered transactions list if app_url is configured."""
    if app_url:
        return f'👉 <a href="{app_url.rstrip("/")}/transactions?needs_review=true">Open transactions to review</a>'
    return "👉 Open transactions to review"


def _transactions_footer(app_url: str, label: str, query: str = "") -> str:
    """Return a 'pending settlement' footer, as a link if app_url is configured."""
    if app_url:
        return f'📌 <a href="{app_url.rstrip("/")}/transactions{query}">{label}</a>'
    return f"📌 {label}."


def send_transaction_ping_fields(fields: dict) -> None:
    """Fire-and-forget variant that takes pre-extracted scalar fields (no ORM/DB access).

    `fields` must include `bot_token`, `chat_id`, and `app_url`, resolved by the
    caller (via `get_telegram_config`) while the DB session was still open.
    The caller is also responsible for populating `telegram_hide_amounts`.
    """
    amount_str = f"{fields['amount']:,.0f}đ"
    direction = "+" if fields["tx_type"] == "income" else "-"
    source_label = {"email": "Email", "ocr": "OCR"}.get(fields["source"], fields["source"])
    desc = fields["description"] or "No description"
    app_url = fields.get("app_url", "")
    hide = fields.get("telegram_hide_amounts", "false") == "true"

    if fields["needs_review"]:
        text = (
            f"⚠️ <b>Needs review</b> [{_esc(source_label)}]\n"
            f"{_amount(direction + amount_str, hide)} — {_esc(fields['cat_name'])}\n"
            f"<i>{_esc(desc)}</i>\n"
            f"{_review_link(app_url)}"
        )
    else:
        text = (
            f"💸 <b>New</b> [{_esc(source_label)}]\n"
            f"{_amount(direction + amount_str, hide)} — {_esc(fields['cat_name'])}\n"
            f"<i>{_esc(desc)}</i>"
        )

    _fire(text, fields["bot_token"], fields["chat_id"])


def send_message(text: str, db: Session) -> bool:
    """Send an arbitrary HTML-formatted message (used by brain pipeline later, and the
    Settings 'send test message' action)."""
    cfg = get_telegram_config(db)
    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])
