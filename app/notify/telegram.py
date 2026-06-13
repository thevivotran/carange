"""Outbound Telegram notifications (Phase 1 — send-only).

Credentials and the optional app URL are resolved per-call via
`app.services.settings_service.get_telegram_config`, which checks the database
first (set via the Settings UI) and falls back to the TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID / APP_URL env vars. If bot token or chat ID are missing,
functions log a debug message and return False.
"""

import logging

import requests
from sqlalchemy.orm import Session

from app.models.database import Transaction
from app.services.settings_service import get_telegram_config

log = logging.getLogger("app.notify.telegram")

_API_BASE = "https://api.telegram.org"
_TIMEOUT = 10


def _send(text: str, bot_token: str, chat_id: str, parse_mode: str = "HTML") -> bool:
    """POST a message to the configured chat. Returns True on success."""
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


def _review_link(app_url: str) -> str:
    """Return the 'open review inbox' line, as a link if app_url is configured."""
    if app_url:
        return f'👉 <a href="{app_url.rstrip("/")}/review">Open the Review Inbox to confirm</a>'
    return "👉 Open the Review Inbox to confirm"


def _transactions_footer(app_url: str, label: str) -> str:
    """Return a 'pending settlement' footer, as a link if app_url is configured."""
    if app_url:
        return f'📌 <a href="{app_url.rstrip("/")}/transactions">{label}</a>'
    return f"📌 {label}."


def send_transaction_ping_fields(fields: dict) -> bool:
    """Fire-and-forget variant that takes pre-extracted scalar fields (no ORM/DB access).

    `fields` must include `bot_token`, `chat_id`, and `app_url`, resolved by the
    caller (via `get_telegram_config`) while the DB session was still open.
    """
    amount_str = f"{fields['amount']:,.0f}đ"
    direction = "+" if fields["tx_type"] == "income" else "-"
    source_label = {"email": "Email", "ocr": "OCR"}.get(fields["source"], fields["source"])
    desc = fields["description"] or "No description"
    app_url = fields.get("app_url", "")

    if fields["needs_review"]:
        text = (
            f"⚠️ <b>Needs review</b> [{source_label}]\n"
            f"{direction}{amount_str} — {fields['cat_name']}\n"
            f"<i>{desc}</i>\n"
            f"{_review_link(app_url)}"
        )
    else:
        text = f"💸 <b>New</b> [{source_label}]\n{direction}{amount_str} — {fields['cat_name']}\n<i>{desc}</i>"

    return _send(text, fields["bot_token"], fields["chat_id"])


def send_transaction_ping(tx: Transaction, db: Session) -> bool:
    """Notify when an auto-ingested transaction is created.

    If the transaction needs review, the message prompts to open the inbox.
    """
    cfg = get_telegram_config(db)
    amount_str = f"{tx.amount:,.0f}đ"
    direction = "+" if tx.type and tx.type.value == "income" else "-"
    cat_name = tx.category.name if tx.category else "?"
    desc = tx.description or "No description"
    source_label = {"email": "Email", "ocr": "OCR"}.get(tx.source or "", tx.source or "manual")

    if tx.needs_review:
        text = (
            f"⚠️ <b>Needs review</b> [{source_label}]\n"
            f"{direction}{amount_str} — {cat_name}\n"
            f"<i>{desc}</i>\n"
            f"{_review_link(cfg['app_url'])}"
        )
    else:
        text = f"💸 <b>New</b> [{source_label}]\n{direction}{amount_str} — {cat_name}\n<i>{desc}</i>"

    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])


def send_review_reminder(count: int, db: Session) -> bool:
    """Notify when the review inbox has been sitting unread."""
    if count <= 0:
        return False
    cfg = get_telegram_config(db)
    plural = "s" if count != 1 else ""
    text = f"📋 <b>{count} transaction{plural}</b> pending review.\n{_review_link(cfg['app_url'])}"
    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])


def send_personal_advance_ping(tx: Transaction, db: Session, action: str = "created") -> bool:
    """Notify when a personal-advance transaction is created or updated (unsettled).

    action: "created" | "updated"
    Only fires when is_advance=True and advance_settled=False.
    """
    if not tx.is_advance or tx.advance_settled:
        return False
    cfg = get_telegram_config(db)
    amount_str = f"{tx.amount:,.0f}đ"
    cat_name = tx.category.name if tx.category else "?"
    desc = tx.description or "No description"
    verb = "Created" if action == "created" else "Updated"
    footer = _transactions_footer(cfg["app_url"], "Pending settlement — view transactions")
    text = f"💳 <b>Personal advance — {verb}</b>\n-{amount_str} — {cat_name}\n<i>{desc}</i>\n{footer}"
    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])


def send_message(text: str, db: Session) -> bool:
    """Send an arbitrary HTML-formatted message (used by brain pipeline later, and the
    Settings 'send test message' action)."""
    cfg = get_telegram_config(db)
    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])
