"""Grab receipt email parser (ride + food)."""

import re
from datetime import datetime

from email_worker.parsers.base import BaseEmailParser, ParsedEmailTransaction


class GrabParser(BaseEmailParser):
    SENDER_PATTERN = re.compile(r"grab", re.IGNORECASE)
    AMOUNT_PATTERN = re.compile(r"(?:total|tổng|bạn trả|fare)[:\s]*([0-9.,]+)\s*(?:đ|VND|VNĐ)?", re.IGNORECASE)
    FOOD_PATTERN = re.compile(r"grabfood|food|giao hàng", re.IGNORECASE)

    def can_parse(self, sender: str, subject: str, body_text: str) -> bool:
        return bool(self.SENDER_PATTERN.search(sender))

    def parse(self, sender: str, subject: str, body_text: str, body_html: str) -> list[ParsedEmailTransaction]:
        m = self.AMOUNT_PATTERN.search(body_text)
        if not m:
            return []
        amount = self._clean_amount(m.group(1))
        if not amount:
            return []

        is_food = bool(self.FOOD_PATTERN.search(body_text + subject))
        desc = "Grab Food" if is_food else "Grab"
        hint = "Food & Dining" if is_food else "Transportation"

        return [
            ParsedEmailTransaction(
                date=datetime.now().date(),
                amount=amount,
                tx_type="expense",
                description=desc,
                confidence=0.85,
                category_hint=hint,
            )
        ]
