"""MIME / HTML extraction and source routing for the email worker."""

import email
import logging
from email.message import Message

from bs4 import BeautifulSoup

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction
from email_worker.parsers.grab import GrabParser
from email_worker.parsers.shopee import ShopeeParser
from email_worker.parsers.vcb import VCBParser
from email_worker.parsers.generic import GenericOllamaParser

log = logging.getLogger("email_worker.email_parser")

# Ordered by specificity — generic must be last
_PARSERS: list[BaseEmailParser] = [
    VCBParser(),
    ShopeeParser(),
    GrabParser(),
    GenericOllamaParser(),
]


def extract_email_parts(raw_message: bytes) -> tuple[str, str, str, str]:
    """Parse a raw RFC 2822 message.

    Returns (message_id, sender, subject, body_text).
    """
    msg: Message = email.message_from_bytes(raw_message)

    message_id = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
    sender = (msg.get("From") or "").strip()
    subject = (msg.get("Subject") or "").strip()

    body_text = ""
    body_html = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not body_text:
                body_text = _decode_part(part)
            elif ct == "text/html" and not body_html:
                body_html = _decode_part(part)
    else:
        ct = msg.get_content_type()
        if ct == "text/html":
            body_html = _decode_part(msg)
        else:
            body_text = _decode_part(msg)

    if not body_text and body_html:
        body_text = _html_to_text(body_html)

    return message_id, sender, subject, body_text


def route_and_parse(
    sender: str, subject: str, body_text: str, body_html: str
) -> tuple[list[ParsedEmailTransaction], str]:
    """Find the first matching parser and return its results + parser name."""
    for parser in _PARSERS:
        if parser.can_parse(sender, subject, body_text):
            results = parser.parse(sender, subject, body_text, body_html)
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


def _html_to_text(html: str) -> str:
    try:
        soup = BeautifulSoup(html, "lxml")
        return soup.get_text(separator="\n")
    except Exception:
        return html
