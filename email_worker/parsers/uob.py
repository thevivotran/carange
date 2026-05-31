"""UOB card transaction alert email parser."""

import re
from datetime import date, datetime

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction


class UOBParser(BaseEmailParser):
    SENDER_PATTERN = re.compile(r"uob|unialerts", re.IGNORECASE)
    SUBJECT_PATTERN = re.compile(r"card transaction|thong bao giao dich the", re.IGNORECASE)

    # English: "transaction of VND 1,076,400 on 30/05/2026"
    EN_AMOUNT = re.compile(r"transaction of VND\s+([0-9][0-9,.]*)\s+on\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
    # Vietnamese (no diacritics): "giao dich 1,076,400 VND vao ngay 30/05/2026"
    VN_AMOUNT = re.compile(r"giao dich\s+([0-9][0-9,.]*)\s+VND\s+vao ngay\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)

    # Card ending pattern: "card ending in 8076"
    CARD_PATTERN = re.compile(r"(?:card ending in|so cuoi)\s+(\d{4})", re.IGNORECASE)

    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return (
            bool(self.SENDER_PATTERN.search(sender))
            or bool(self.SUBJECT_PATTERN.search(subject))
            or "uobgroup.com" in body_text.lower()
            or "uob tmrw" in body_text.lower()
        )

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        amount, tx_date = self._extract_amount_and_date(body_text)
        if not amount:
            return []

        card_m = self.CARD_PATTERN.search(body_text)
        card_suffix = card_m.group(1) if card_m else ""
        desc = f"UOB Card *{card_suffix}" if card_suffix else "UOB Card"

        return [
            ParsedEmailTransaction(
                date=tx_date,
                amount=amount,
                tx_type="expense",
                description=desc,
                confidence=0.90,
                category_hint="Others",
                payment_method="credit_card",
            )
        ]

    def _extract_amount_and_date(self, text: str) -> tuple:
        for pattern in (self.EN_AMOUNT, self.VN_AMOUNT):
            m = pattern.search(text)
            if m:
                amount = self._clean_amount(m.group(1))
                tx_date = self._parse_date(m.group(2))
                if amount:
                    return amount, tx_date
        return None, datetime.now().date()

    def _parse_date(self, date_str: str) -> date:
        try:
            day, mon, year = date_str.split("/")
            return date(int(year), int(mon), int(day))
        except (ValueError, AttributeError):
            return datetime.now().date()
