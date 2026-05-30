"""Outbound Telegram notifications (Phase 1 — send-only).

Requires env vars:
  TELEGRAM_BOT_TOKEN  — bot token from @BotFather
  TELEGRAM_CHAT_ID    — target chat/group ID

Both are optional: if either is missing, functions log a warning and return.
"""

import logging
import os

import requests

from app.models.database import Transaction

log = logging.getLogger("app.notify.telegram")

_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_API_BASE = "https://api.telegram.org"
_TIMEOUT = 10


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """POST a message to the configured chat. Returns True on success."""
    if not _BOT_TOKEN or not _CHAT_ID:
        log.debug("Telegram not configured — skipping notification")
        return False
    try:
        resp = requests.post(
            f"{_API_BASE}/bot{_BOT_TOKEN}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            log.warning("Telegram API error %d: %s", resp.status_code, resp.text[:200])
        return resp.ok
    except requests.RequestException as exc:
        log.warning("Telegram send failed: %s", exc)
        return False


def send_transaction_ping(tx: Transaction) -> bool:
    """Notify when an auto-ingested transaction is created.

    If the transaction needs review, the message links to /review.
    """
    amount_str = f"{tx.amount:,.0f}đ"
    direction = "+" if tx.type and tx.type.value == "income" else "-"
    cat_name = tx.category.name if tx.category else "?"
    desc = tx.description or "Không có mô tả"
    source_label = {"email": "Email", "ocr": "OCR"}.get(tx.source or "", tx.source or "manual")

    if tx.needs_review:
        text = (
            f"⚠️ <b>Cần xem lại</b> [{source_label}]\n"
            f"{direction}{amount_str} — {cat_name}\n"
            f"<i>{desc}</i>\n"
            f"👉 <a href='/review'>Xem hộp thư</a>"
        )
    else:
        text = f"💸 <b>Mới</b> [{source_label}]\n{direction}{amount_str} — {cat_name}\n<i>{desc}</i>"

    return _send(text)


def send_review_reminder(count: int) -> bool:
    """Notify when the review inbox has been sitting unread."""
    if count <= 0:
        return False
    text = f"📋 <b>{count} giao dịch</b> đang chờ xác nhận.\n👉 <a href='/review'>Mở hộp thư</a>"
    return _send(text)


def send_message(text: str) -> bool:
    """Send an arbitrary HTML-formatted message (used by brain pipeline later)."""
    return _send(text)
