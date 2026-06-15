"""Outbound Telegram notifications (Phase 1 — send-only).

Credentials and the optional app URL are resolved per-call via
`app.services.settings_service.get_telegram_config`, which checks the database
first (set via the Settings UI) and falls back to the TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID / APP_URL env vars. If bot token or chat ID are missing,
functions log a debug message and return False.
"""

import html
import logging

import requests
from sqlalchemy.orm import Session

from app.models.database import Transaction
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
    """Return the 'needs review' line, linking to the filtered transactions list if app_url is configured."""
    if app_url:
        return f'👉 <a href="{app_url.rstrip("/")}/transactions?needs_review=true">Open transactions to review</a>'
    return "👉 Open transactions to review"


def _transactions_footer(app_url: str, label: str, query: str = "") -> str:
    """Return a 'pending settlement' footer, as a link if app_url is configured."""
    if app_url:
        return f'📌 <a href="{app_url.rstrip("/")}/transactions{query}">{label}</a>'
    return f"📌 {label}."


def send_transaction_ping_fields(fields: dict) -> bool:
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
    hide = cfg.get("telegram_hide_amounts") == "true"

    if tx.needs_review:
        text = (
            f"⚠️ <b>Needs review</b> [{_esc(source_label)}]\n"
            f"{_amount(direction + amount_str, hide)} — {_esc(cat_name)}\n"
            f"<i>{_esc(desc)}</i>\n"
            f"{_review_link(cfg['app_url'])}"
        )
    else:
        text = (
            f"💸 <b>New</b> [{_esc(source_label)}]\n"
            f"{_amount(direction + amount_str, hide)} — {_esc(cat_name)}\n"
            f"<i>{_esc(desc)}</i>"
        )

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
    footer = _transactions_footer(cfg["app_url"], "Pending settlement — view transactions", "?advance=unsettled")
    hide = cfg.get("telegram_hide_amounts") == "true"
    text = (
        f"💳 <b>Personal advance — {_esc(verb)}</b>\n"
        f"{_amount('-' + amount_str, hide)} — {_esc(cat_name)}\n"
        f"<i>{_esc(desc)}</i>\n"
        f"{footer}"
    )
    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])


def send_budget_threshold_alert(
    category_name: str, spent: float, limit: float, pct: float, threshold: int, db: Session
) -> bool:
    cfg = get_telegram_config(db)
    hide = cfg.get("telegram_hide_amounts") == "true"
    spent_str = f"{spent:,.0f}đ"
    limit_str = f"{limit:,.0f}đ"
    status_line = "Over budget!" if threshold >= 100 else "Approaching budget limit"
    text = (
        f"🚨 <b>Budget Alert</b> — {_esc(category_name)}\n"
        f"{_amount(spent_str, hide)} / {_amount(limit_str, hide)} (<b>{pct:.0f}%</b>)\n"
        f"{status_line}\n"
        f"{_budget_link(cfg['app_url'])}"
    )
    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])


def send_message(text: str, db: Session) -> bool:
    """Send an arbitrary HTML-formatted message (used by brain pipeline later, and the
    Settings 'send test message' action)."""
    cfg = get_telegram_config(db)
    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])
