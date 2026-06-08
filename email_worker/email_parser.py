"""MIME / HTML extraction and source routing for the email worker."""

import email
import logging
import re
from email.message import Message

from bs4 import BeautifulSoup

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction
from email_worker.parsers.grab import GrabParser
from email_worker.parsers.shopee import ShopeeParser
from email_worker.parsers.timo import TimoParser
from email_worker.parsers.vcb import VCBParser
from email_worker.parsers.uob import UOBParser
from email_worker.parsers.payoo import PayooParser
from email_worker.parsers.vnpay import VNPayParser
from email_worker.parsers.learned import LearnedRegexParser
from email_worker.parsers.generic import GenericOllamaParser

log = logging.getLogger("email_worker.email_parser")

# Ordered by specificity — LearnedRegexParser before generic LLM fallback
_PARSERS: list[BaseEmailParser] = [
    VCBParser(),
    UOBParser(),
    PayooParser(),
    VNPayParser(),
    ShopeeParser(),
    GrabParser(),
    TimoParser(),
    LearnedRegexParser(),
    GenericOllamaParser(),
]

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_WROTE_RE = re.compile(r"<([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>\s+wrote:", re.IGNORECASE)
_FROM_HDR_RE = re.compile(
    r"^From:\s+.*?<([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>",
    re.MULTILINE | re.IGNORECASE,
)
_QUOTE_RE = re.compile(r"^(>\s*)+", re.MULTILINE)

# Detects text/plain parts that are actually mislabeled HTML source or CSS
# rule fragments — seen from senders like Payoo whose multipart/alternative
# emails ship broken "plain text" alternatives alongside a good text/html part.
_MARKUP_SOUP_RE = re.compile(
    r"<!doctype\s+html|<html[\s>]|<body[\s>]|<table[\s>]|<tbody[\s>]|<tr[\s>]|"
    r"<td[\s>]|<div[\s>]|<style[\s>]|<span[\s>]|"
    r"[.#]?[\w-]+\s*\{[^{}]*:[^{}]*\}|"
    r"@media\b|@font-face\b",
    re.IGNORECASE,
)


def _unwrap_forwarded(body_text: str) -> tuple[str, str]:
    """Strip > quoting and extract the original sender from forwarded/reply emails.

    Returns (original_sender, clean_body). When no forwarding is detected,
    original_sender is '' and clean_body equals body_text.
    """
    original_sender = ""

    # Prefer the innermost "On ... <email> wrote:" sender (deepest nesting = original source)
    for m in _WROTE_RE.finditer(body_text):
        original_sender = m.group(1)  # last match wins → deepest quoted level

    if not original_sender:
        fm = _FROM_HDR_RE.search(body_text)
        if fm:
            original_sender = fm.group(1)

    # Strip all leading > chains from every line
    clean_body = _QUOTE_RE.sub("", body_text)
    return original_sender, clean_body


def extract_email_parts(raw_message: bytes) -> tuple[str, str, str, str, str]:
    """Parse a raw RFC 2822 message.

    Returns (message_id, sender, subject, body_text, body_html).
    Both body_text and body_html are populated when available.
    body_text is derived from body_html via BeautifulSoup when no
    plaintext part exists — but body_html is still returned so
    parsers can use the original HTML for structured extraction
    (e.g. pickup/dropoff addresses in Grab receipts).
    """
    msg: Message = email.message_from_bytes(raw_message)

    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
    sender = (msg.get("From") or "").strip()
    subject = (msg.get("Subject") or "").strip()

    body_text = ""
    body_html = ""

    if msg.is_multipart():
        plain_parts = []
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                plain_parts.append(_decode_part(part))
            elif ct == "text/html" and not body_html:
                body_html = _decode_part(part)

        # Prefer the first plain part that looks like real prose. Some senders
        # (e.g. Payoo) ship multipart/alternative messages where every
        # text/plain alternative is mislabeled HTML/CSS soup — in that case
        # fall through to converting the text/html part below.
        body_text = next((t for t in plain_parts if t.strip() and not _looks_like_markup_soup(t)), "")
        if not body_text and plain_parts and not body_html:
            body_text = plain_parts[0]
    else:
        ct = msg.get_content_type()
        if ct == "text/html":
            body_html = _decode_part(msg)
        else:
            body_text = _decode_part(msg)

    if not body_text and body_html:
        body_text = _html_to_text(body_html)

    return message_id, sender, subject, body_text, body_html


def route_and_parse(
    sender: str, subject: str, body_text: str, body_html: str
) -> tuple[list[ParsedEmailTransaction], str]:
    """Find the first matching parser and return its results + parser name."""
    original_sender, clean_body = _unwrap_forwarded(body_text)
    effective_sender = original_sender or sender
    log.debug("route_and_parse: envelope_sender=%s original_sender=%s", sender, original_sender)

    for parser in _PARSERS:
        if parser.can_parse(effective_sender, subject, clean_body):
            results = parser.parse(effective_sender, subject, clean_body, body_html)
            parser_name = type(parser).__name__
            log.info("Parser %s found %d transaction(s)", parser_name, len(results))
            if results:
                return results, parser_name
    return [], "none"


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _looks_like_markup_soup(text: str) -> bool:
    """True when a "text/plain" part is actually mislabeled HTML source or
    CSS rule fragments rather than human-readable prose.

    A handful of incidental matches (e.g. one stray ``<div>`` quoted from a
    forwarded HTML snippet) is normal in real plaintext; dense markup/CSS
    indicates the whole part is unusable soup.
    """
    if not text.strip():
        return True
    return len(_MARKUP_SOUP_RE.findall(text)) >= 3


def _html_to_text(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        return soup.get_text(separator="\n")
    except Exception:
        return html
