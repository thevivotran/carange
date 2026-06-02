"""Timo Digital Bank transaction notification email parser."""

import re
from datetime import date, datetime

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction

# Matches: "has been debited 37,000 VND on 02/06/2026 08:39"
#       or: "has been credited 165,000 VND on 31/05/2026 13:25"
_TXN_RE = re.compile(
    r"has been (debited|credited)\s+([\d,\.]+)\s+VND\s+on\s+(\d{2}/\d{2}/\d{4})",
    re.IGNORECASE,
)

# Matches: "Transaction Description: 7Eleven MXN 517557."
_DESC_RE = re.compile(r"Transaction Description:\s*(.+?)\.?\s*$", re.IGNORECASE | re.MULTILINE)


def _parse_date(date_str: str) -> date:
    try:
        day, mon, year = date_str.split("/")
        return date(int(year), int(mon), int(day))
    except (ValueError, AttributeError):
        return datetime.now().date()


class TimoParser(BaseEmailParser):
    SENDER_PATTERN = re.compile(r"timo\.vn", re.IGNORECASE)
    SUBJECT_PATTERN = re.compile(r"(debit|credit)\s+transaction\s+notice", re.IGNORECASE)

    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return bool(self.SENDER_PATTERN.search(sender)) or bool(self.SUBJECT_PATTERN.search(subject))

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        m = _TXN_RE.search(body_text)
        if not m:
            return []

        direction, raw_amount, raw_date = m.group(1).lower(), m.group(2), m.group(3)
        amount = self._clean_amount(raw_amount)
        if not amount:
            return []

        tx_date = _parse_date(raw_date)
        tx_type = "expense" if direction == "debited" else "income"

        desc_m = _DESC_RE.search(body_text)
        description = desc_m.group(1).strip() if desc_m else subject

        return [
            ParsedEmailTransaction(
                date=tx_date,
                amount=amount,
                tx_type=tx_type,
                description=description,
                confidence=0.95,
                category_hint="Others",
                payment_method="bank_transfer",
            )
        ]
