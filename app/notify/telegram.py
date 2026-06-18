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
import urllib3.util.connection as _urllib3_conn
from sqlalchemy.orm import Session

# flannel CNI in k8s pods has no IPv6 egress; force urllib3 to use IPv4 only
# so DNS responses that include AAAA records don't cause ENETUNREACH failures.
_urllib3_conn.HAS_IPV6 = False

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


def _send(text: str, bot_token: str, chat_id: str, parse_mode: str = "HTML", reply_markup: dict | None = None) -> bool:
    """POST a message to the configured chat. Returns True on success (blocking)."""
    if not bot_token or not chat_id:
        log.debug("Telegram not configured — skipping notification")
        return False
    try:
        payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        resp = requests.post(
            f"{_API_BASE}/bot{bot_token}/sendMessage",
            json=payload,
            timeout=_TIMEOUT,
        )
        if not resp.ok:
            log.warning("Telegram API error %d: %s", resp.status_code, resp.text[:200])
        return resp.ok
    except requests.RequestException as exc:
        log.warning("Telegram send failed: %s", exc)
        return False


def _fire(text: str, bot_token: str, chat_id: str, parse_mode: str = "HTML", reply_markup: dict | None = None) -> None:
    """Non-blocking variant of _send — spawns a daemon thread so the caller returns immediately."""
    if not bot_token or not chat_id:
        log.debug("Telegram not configured — skipping notification")
        return
    threading.Thread(target=_send, args=(text, bot_token, chat_id, parse_mode, reply_markup), daemon=True).start()


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


def inline_url_keyboard(app_url: str, items: list[tuple[str, str]]) -> dict | None:
    """Build an inline keyboard dict from (label, path) pairs. Returns None when app_url is empty.

    Buttons are laid out in rows of up to 2.
    """
    if not app_url:
        return None
    base = app_url.rstrip("/")
    rows: list[list[dict]] = []
    for i in range(0, len(items), 2):
        row = []
        for label, path in items[i : i + 2]:
            row.append({"text": label, "url": base + path})
        rows.append(row)
    return {"inline_keyboard": rows}


def _budget_bar_line(snapshot: dict) -> str:
    """Render a budget bar line from a snapshot dict: `{bar} {usage_pct}%  {status}`."""
    from app.services.budget_context import render_bar

    pct = snapshot.get("projected_usage_pct", snapshot.get("usage_pct", 0))
    status = snapshot.get("projected_status", snapshot.get("status", ""))
    bar = render_bar(pct)
    suffix = " ⚠️" if pct >= 100 else ""
    return f"{bar} {pct:.0f}%  {status}{suffix}"


def _build_card_text(
    header: str,
    body_lines: list[str],
    snapshot: dict | None = None,
) -> str:
    """Assemble a card: header, divider, body lines, optional budget bar."""
    parts = [f"<b>{header}</b>", "———"]
    parts.extend(body_lines)
    if snapshot:
        parts.append(_budget_bar_line(snapshot))
    return "\n".join(parts)


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
    tx_id = fields.get("tx_id")

    snapshot = fields.get("budget_snapshot")

    amount_line = _amount(f"{direction}{amount_str} — {_esc(fields['cat_name'])}", hide)

    if fields["needs_review"]:
        header = f"⚠️ Needs review [{_esc(source_label)}]"
        body_lines = [f"<b>{amount_line}</b>", f"<i>{_esc(desc)}</i>"]
        text = _build_card_text(header, body_lines, snapshot)
        keyboard_items = [
            ("📥 Review inbox", "/transactions?needs_review=true"),
        ]
        if tx_id:
            keyboard_items.append(("🔍 View", f"/transactions?focus={tx_id}"))
        keyboard_items.append(("📊 View budget", "/budget"))
    else:
        header = f"💸 New [{_esc(source_label)}]"
        body_lines = [f"<b>{amount_line}</b>", f"<i>{_esc(desc)}</i>"]
        text = _build_card_text(header, body_lines, snapshot)
        keyboard_items = []
        if tx_id:
            keyboard_items.append(("🔍 View", f"/transactions?focus={tx_id}"))
        keyboard_items.append(("📊 View budget", "/budget"))

    markup = inline_url_keyboard(app_url, keyboard_items)
    _fire(text, fields["bot_token"], fields["chat_id"], reply_markup=markup)


def send_message(text: str, db: Session) -> bool:
    """Send an arbitrary HTML-formatted message (used by brain pipeline later, and the
    Settings 'send test message' action)."""
    cfg = get_telegram_config(db)
    return _send(text, cfg["telegram_bot_token"], cfg["telegram_chat_id"])
